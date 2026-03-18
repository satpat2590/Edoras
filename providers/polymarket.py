#!/usr/bin/env python3
"""
Polymarket data provider — fetches market metadata, price history, and order books
from the Polymarket Gamma API and CLOB API (no authentication required).

Usage:
    from providers.polymarket import PolymarketProvider
    pm = PolymarketProvider(db_path="crypto_data.db")
    pm.sync_markets(limit=50)               # discover & register markets
    pm.ingest_prices("PM:IRAN-HORMUZ-MAR")  # fetch price history → candlesticks
"""

import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
USER_AGENT = "PolymarketProvider/1.0"

# Map Gamma API tag strings to sectors
TAG_SECTOR_MAP = {
    "politics": "politics",
    "crypto": "crypto",
    "sports": "sports",
    "science": "science",
    "finance": "macro",
    "economics": "macro",
    "fed": "macro",
    "pop-culture": "culture",
    "ai": "tech",
    "technology": "tech",
    "business": "business",
    "geopolitics": "geopolitics",
    "climate": "climate",
}


def _get_json(url: str, timeout: int = 15) -> dict:
    """Fetch JSON from a URL with standard headers."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def _slug_to_symbol(slug: str, prefix: str = "PM") -> str:
    """Convert a Polymarket slug to a canonical symbol.

    e.g. 'will-iran-close-strait-of-hormuz' → 'PM:IRAN-STRAIT-HORMUZ'
    Keeps it short and uppercase for consistency with other symbols.
    """
    # Remove common prefixes
    slug = slug.lower()
    for noise in ("will-", "will-the-", "the-", "a-"):
        if slug.startswith(noise):
            slug = slug[len(noise):]
    # Truncate, uppercase, clean
    parts = slug.split("-")[:5]
    short = "-".join(parts).upper()
    return f"{prefix}:{short}"


def _classify_sector(tags: list, question: str) -> str:
    """Derive sector from market tags and question text."""
    if tags:
        for tag in tags:
            tag_lower = tag.lower() if isinstance(tag, str) else ""
            if tag_lower in TAG_SECTOR_MAP:
                return TAG_SECTOR_MAP[tag_lower]
    # Keyword fallback
    q = question.lower()
    if any(w in q for w in ("fed", "rate", "inflation", "gdp", "cpi")):
        return "macro"
    if any(w in q for w in ("trump", "biden", "election", "senate", "congress")):
        return "politics"
    if any(w in q for w in ("bitcoin", "btc", "eth", "crypto")):
        return "crypto"
    if any(w in q for w in ("nba", "nfl", "mlb", "premier league", "champions")):
        return "sports"
    return "other"


class PolymarketProvider:
    """Fetches and stores Polymarket data in the financial data warehouse."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crypto_data.db")
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_exchange_id(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT id FROM exchanges WHERE code='polymarket'").fetchone()
        if not row:
            raise RuntimeError("polymarket exchange not found in exchanges table")
        return row["id"]

    # ── Market discovery ──────────────────────────────────────────────────

    def fetch_markets(self, limit: int = 50, min_volume_24h: float = 100_000) -> List[dict]:
        """Fetch active markets from Gamma API, filtered by volume."""
        url = f"{GAMMA_API}/markets?limit={limit}&active=true&order=volume24hr&ascending=false"
        markets = _get_json(url)
        return [m for m in markets if float(m.get("volume24hr", 0)) >= min_volume_24h]

    def sync_markets(self, limit: int = 50, min_volume_24h: float = 100_000) -> int:
        """Discover markets and register them as securities.

        Returns number of new securities created.
        """
        markets = self.fetch_markets(limit=limit, min_volume_24h=min_volume_24h)
        conn = self._connect()
        exchange_id = self._get_exchange_id(conn)
        created = 0

        for m in markets:
            slug = m.get("slug") or m.get("market_slug", "unknown")
            symbol = _slug_to_symbol(slug)
            question = m.get("question", "")
            end_date = m.get("endDate", "")[:10] if m.get("endDate") else None
            tags = m.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            sector = _classify_sector(tags, question)

            clob_ids = m.get("clobTokenIds", "[]")
            if isinstance(clob_ids, str):
                clob_ids = json.loads(clob_ids)
            outcomes = m.get("outcomes", "[]")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            metadata = {
                "condition_id": m.get("conditionId"),
                "clob_token_ids": clob_ids,
                "outcomes": outcomes,
                "gamma_id": m.get("id"),
                "slug": slug,
                "volume_total": float(m.get("volume", 0)),
                "volume_24h": float(m.get("volume24hr", 0)),
                "liquidity": float(m.get("liquidity", 0)),
            }

            existing = conn.execute(
                "SELECT id FROM securities WHERE symbol=? AND exchange_id=?",
                (symbol, exchange_id),
            ).fetchone()

            if existing:
                # Update metadata (volumes change)
                conn.execute(
                    "UPDATE securities SET metadata_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(metadata), existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO securities "
                    "(symbol, name, security_type, exchange_id, asset_class, sector, "
                    "quote_currency, price_min, price_max, tick_size, settlement_type, "
                    "expiry_date, indicator_profile, metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        symbol, question, "prediction_binary", exchange_id,
                        "prediction", sector, "USDC",
                        0.0, 1.0, 0.01, "binary_expiry",
                        end_date, "binary", json.dumps(metadata),
                    ),
                )
                created += 1
                logger.info(f"Registered: {symbol} — {question[:60]}")

        conn.commit()
        conn.close()
        return created

    # ── Price ingestion ───────────────────────────────────────────────────

    def ingest_prices(self, symbol: str, interval: str = "1h", days: int = 30) -> int:
        """Fetch price history for a security and write to candlesticks table.

        For binary markets, OHLCV is synthesized from the price timeseries:
        - open/close = price at interval boundaries
        - high/low = max/min within interval
        - volume = 0 (not available from price history endpoint)

        Returns number of candles written.
        """
        conn = self._connect()
        exchange_id = self._get_exchange_id(conn)

        row = conn.execute(
            "SELECT metadata_json FROM securities WHERE symbol=? AND exchange_id=?",
            (symbol, exchange_id),
        ).fetchone()
        if not row:
            logger.error(f"Security {symbol} not found in securities table")
            conn.close()
            return 0

        metadata = json.loads(row["metadata_json"])
        clob_ids = metadata.get("clob_token_ids", [])
        if not clob_ids:
            logger.error(f"No CLOB token IDs for {symbol}")
            conn.close()
            return 0

        # Use YES token (index 0) for price history
        yes_token = clob_ids[0]
        end_ts = int(time.time())
        start_ts = end_ts - days * 86400

        url = (
            f"{CLOB_API}/prices-history?market={yes_token}"
            f"&startTs={start_ts}&endTs={end_ts}&interval={interval}&fidelity=60"
        )
        try:
            data = _get_json(url)
        except Exception as e:
            logger.error(f"Failed to fetch price history for {symbol}: {e}")
            conn.close()
            return 0

        history = data.get("history", [])
        if not history:
            logger.warning(f"No price history returned for {symbol}")
            conn.close()
            return 0

        # Map interval string to timeframe for candlesticks table
        tf_map = {"1h": "1h", "6h": "4h", "1d": "1d", "1w": "1w"}
        timeframe = tf_map.get(interval, "1h")

        # Build OHLCV candles from the price series
        # Group points by interval boundaries
        interval_seconds = {"1h": 3600, "6h": 21600, "1d": 86400, "1w": 604800}.get(interval, 3600)

        candles = {}
        for pt in history:
            ts = pt["t"]
            price = pt["p"]
            bucket = (ts // interval_seconds) * interval_seconds
            if bucket not in candles:
                candles[bucket] = {"open": price, "high": price, "low": price, "close": price, "volume": 0}
            else:
                c = candles[bucket]
                c["high"] = max(c["high"], price)
                c["low"] = min(c["low"], price)
                c["close"] = price  # last price in bucket

        # Write to DB
        written = 0
        cur = conn.cursor()
        for ts, c in sorted(candles.items()):
            cur.execute(
                "INSERT OR REPLACE INTO candlesticks "
                "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (symbol, timeframe, ts, c["open"], c["high"], c["low"], c["close"], c["volume"]),
            )
            written += 1

        conn.commit()
        conn.close()
        logger.info(f"Ingested {written} {timeframe} candles for {symbol}")
        return written

    def ingest_all_active(self, interval: str = "1h", days: int = 7) -> Dict[str, int]:
        """Ingest prices for all active Polymarket securities."""
        conn = self._connect()
        exchange_id = self._get_exchange_id(conn)
        rows = conn.execute(
            "SELECT symbol FROM securities WHERE exchange_id=? AND is_active=1",
            (exchange_id,),
        ).fetchall()
        conn.close()

        results = {}
        for row in rows:
            sym = row["symbol"]
            try:
                n = self.ingest_prices(sym, interval=interval, days=days)
                results[sym] = n
                time.sleep(0.5)  # rate limit courtesy
            except Exception as e:
                logger.error(f"Failed {sym}: {e}")
                results[sym] = 0
        return results

    # ── Order book snapshot ───────────────────────────────────────────────

    def fetch_order_book(self, symbol: str) -> Optional[dict]:
        """Fetch current order book for a security."""
        conn = self._connect()
        exchange_id = self._get_exchange_id(conn)
        row = conn.execute(
            "SELECT metadata_json FROM securities WHERE symbol=? AND exchange_id=?",
            (symbol, exchange_id),
        ).fetchone()
        conn.close()

        if not row:
            return None
        metadata = json.loads(row["metadata_json"])
        clob_ids = metadata.get("clob_token_ids", [])
        if not clob_ids:
            return None

        yes_token = clob_ids[0]
        return _get_json(f"{CLOB_API}/book?token_id={yes_token}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    pm = PolymarketProvider()

    print("=== Discovering markets ===")
    created = pm.sync_markets(limit=20, min_volume_24h=500_000)
    print(f"New securities registered: {created}")

    # Show what we registered
    conn = pm._connect()
    rows = conn.execute(
        "SELECT s.symbol, s.name, s.sector, s.expiry_date "
        "FROM securities s JOIN exchanges e ON s.exchange_id=e.id "
        "WHERE e.code='polymarket' AND s.is_active=1 "
        "ORDER BY s.symbol"
    ).fetchall()
    print(f"\n=== Polymarket securities ({len(rows)}) ===")
    for r in rows:
        print(f"  {r['symbol']:<30} {r['sector']:<12} exp={r['expiry_date']}  {r['name'][:55]}")

    # Ingest price history for all
    print("\n=== Ingesting price history ===")
    results = pm.ingest_all_active(interval="1h", days=7)
    for sym, count in results.items():
        print(f"  {sym}: {count} candles")

    # Verify in DB
    total = sum(results.values())
    print(f"\nTotal candles written: {total}")
    conn.close()
