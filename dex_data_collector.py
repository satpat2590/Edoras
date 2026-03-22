#!/usr/bin/env python3
"""
DEX token data collector — OHLCV candles + metadata from GeckoTerminal.

Fetches candlestick data for DEX-listed tokens and inserts into the existing
`candlesticks` table so the standard indicator pipeline works unchanged.

Also updates `dex_tokens` metadata (liquidity, volume, holder count).

Data source: GeckoTerminal API (free, no auth required)
  - OHLCV: /networks/{chain}/pools/{pool}/ohlcv/{timeframe}
  - Pool info: /networks/{chain}/tokens/{address}/pools

Usage:
    python3 dex_data_collector.py                  # Incremental update (last 24h)
    python3 dex_data_collector.py --backfill 60    # Backfill 60 days
    python3 dex_data_collector.py --metadata-only  # Just update dex_tokens metadata
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, DEX_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
# GeckoTerminal rate limit: ~30 requests/min on free tier
RATE_LIMIT_DELAY = 4  # seconds between requests (GeckoTerminal free tier)


class DexDataCollector:
    """Collect OHLCV and metadata for DEX tokens via GeckoTerminal."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._pool_cache: Dict[str, dict] = {}  # symbol -> pool info

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_dex_tokens(self) -> List[dict]:
        """Load active DEX tokens with their metadata."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT s.symbol, s.chain, s.contract_address, s.name,
                   dt.dex_platform, dt.pair_address, dt.liquidity
            FROM securities s
            JOIN dex_tokens dt ON dt.security_id = s.id
            WHERE s.is_dex = 1 AND s.is_active = 1
              AND s.contract_address IS NOT NULL
              AND s.contract_address != '0x'
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _get_with_retry(self, url: str, params: dict = None,
                        max_retries: int = 3) -> requests.Response:
        """GET with rate limit delay and retry on 429."""
        for attempt in range(max_retries):
            time.sleep(RATE_LIMIT_DELAY)
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                wait = RATE_LIMIT_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited, waiting {wait:.0f}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()  # raise the last 429
        return resp

    # ── Pool discovery ─────────────────────────────────────────────────────

    def find_best_pool(self, chain: str, contract_address: str) -> Optional[dict]:
        """Find the highest-liquidity pool for a token on GeckoTerminal."""
        cache_key = f"{chain}:{contract_address}"
        if cache_key in self._pool_cache:
            return self._pool_cache[cache_key]

        network = "base" if chain == "base" else "eth" if chain == "ethereum" else chain
        url = f"{GECKO_BASE}/networks/{network}/tokens/{contract_address}/pools"

        try:
            resp = self._get_with_retry(url, params={"page": 1})
            pools = resp.json().get("data", [])
        except requests.RequestException as e:
            logger.error(f"Pool discovery failed for {contract_address}: {e}")
            return None

        if not pools:
            return None

        # Pick highest liquidity pool
        best = max(pools, key=lambda p: float(
            p.get("attributes", {}).get("reserve_in_usd", 0) or 0
        ))
        attrs = best.get("attributes", {})
        pool_info = {
            "address": attrs.get("address"),
            "name": attrs.get("name"),
            "reserve_usd": float(attrs.get("reserve_in_usd", 0) or 0),
            "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0) or 0),
            "network": network,
        }
        self._pool_cache[cache_key] = pool_info
        return pool_info

    # ── OHLCV collection ───────────────────────────────────────────────────

    def fetch_ohlcv(self, network: str, pool_address: str,
                    timeframe: str = "hour", aggregate: int = 1,
                    limit: int = 100) -> List[dict]:
        """Fetch OHLCV candles from GeckoTerminal.

        Timeframes: 'minute' (1/5/15), 'hour' (1/4/12), 'day' (1)
        Returns list of {timestamp, open, high, low, close, volume}
        """
        url = f"{GECKO_BASE}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}"
        params = {"aggregate": aggregate, "limit": min(limit, 1000)}

        try:
            resp = self._get_with_retry(url, params=params)
            raw = resp.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        except requests.RequestException as e:
            logger.error(f"OHLCV fetch failed for {pool_address}: {e}")
            return []

        candles = []
        for c in raw:
            if len(c) >= 6:
                candles.append({
                    "timestamp": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                })
        return candles

    def save_candlesticks(self, symbol: str, timeframe: str, candles: List[dict]) -> int:
        """Insert candles into the candlesticks table. Returns count inserted."""
        if not candles:
            return 0

        conn = self._get_conn()
        inserted = 0
        for c in candles:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO candlesticks
                        (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, timeframe, c["timestamp"],
                    c["open"], c["high"], c["low"], c["close"], c["volume"],
                ))
                inserted += conn.total_changes
            except sqlite3.IntegrityError:
                pass
        conn.commit()
        conn.close()
        return inserted

    # ── Metadata update ────────────────────────────────────────────────────

    def update_token_metadata(self, symbol: str, chain: str,
                              contract_address: str) -> Optional[dict]:
        """Update dex_tokens table with latest liquidity/volume/holder data."""
        pool = self.find_best_pool(chain, contract_address)
        if not pool:
            return None

        conn = self._get_conn()
        conn.execute("""
            UPDATE dex_tokens SET
                liquidity = ?,
                volume_24h = ?,
                pair_address = ?,
                last_updated = ?
            WHERE security_id = (SELECT id FROM securities WHERE symbol = ?)
        """, (
            pool["reserve_usd"], pool["volume_24h"],
            pool["address"], datetime.now().isoformat(), symbol,
        ))
        conn.commit()
        conn.close()

        logger.info(f"  {symbol}: liquidity=${pool['reserve_usd']:.0f} "
                     f"vol24h=${pool['volume_24h']:.0f} pool={pool['address'][:10]}...")
        return pool

    # ── Main collection flow ───────────────────────────────────────────────

    def collect_all(self, backfill_days: int = 1):
        """Collect OHLCV candles for all active DEX tokens."""
        tokens = self._get_dex_tokens()
        if not tokens:
            logger.warning("No active DEX tokens found")
            return

        logger.info(f"Collecting data for {len(tokens)} DEX tokens "
                     f"(backfill={backfill_days}d)")

        # Timeframe mapping: system timeframe -> GeckoTerminal params
        tf_map = {
            "1h": ("hour", 1),
            "4h": ("hour", 4),
            "1d": ("day", 1),
        }

        for token in tokens:
            symbol = token["symbol"]
            chain = token["chain"]
            contract = token["contract_address"]
            logger.info(f"\n--- {symbol} ({chain}) ---")

            # 1. Update metadata + find pool
            pool = self.update_token_metadata(symbol, chain, contract)
            if not pool:
                logger.warning(f"  No pool found for {symbol}, skipping")
                continue

            pool_address = pool["address"]
            network = pool["network"]

            # 2. Fetch OHLCV for each timeframe
            for tf, (gecko_tf, agg) in tf_map.items():
                # Calculate how many candles we need
                if tf == "1h":
                    limit = min(backfill_days * 24, 1000)
                elif tf == "4h":
                    limit = min(backfill_days * 6, 1000)
                else:  # 1d
                    limit = min(backfill_days, 1000)

                candles = self.fetch_ohlcv(network, pool_address, gecko_tf, agg, limit)
                inserted = self.save_candlesticks(symbol, tf, candles)
                logger.info(f"  {tf}: fetched {len(candles)}, inserted {inserted}")

        # 3. Compute indicators for DEX symbols (candles are useless without them)
        try:
            from crypto_data_collector import CryptoDataCollector
            ind_calc = CryptoDataCollector(db_path=self.db_path)
            for token in tokens:
                sym = token["symbol"]
                for tf in tf_map:
                    try:
                        n = ind_calc.calculate_indicators(sym, tf)
                        if n and n > 0:
                            logger.info(f"  {sym} {tf}: {n} indicator rows computed")
                    except Exception as e:
                        logger.debug(f"  {sym} {tf} indicators skipped: {e}")
        except Exception as e:
            logger.warning(f"Indicator computation failed: {e}")

        logger.info("\n=== Collection complete ===")

    def metadata_only(self):
        """Update only the dex_tokens metadata (no candlesticks)."""
        tokens = self._get_dex_tokens()
        logger.info(f"Updating metadata for {len(tokens)} DEX tokens")
        for token in tokens:
            self.update_token_metadata(token["symbol"], token["chain"],
                                       token["contract_address"])


def main():
    parser = argparse.ArgumentParser(description="DEX Data Collector")
    parser.add_argument("--backfill", type=int, default=1,
                        help="Days to backfill (default: 1)")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Only update token metadata, no candles")
    args = parser.parse_args()

    collector = DexDataCollector()

    if args.metadata_only:
        collector.metadata_only()
    else:
        collector.collect_all(backfill_days=args.backfill)


if __name__ == "__main__":
    main()
