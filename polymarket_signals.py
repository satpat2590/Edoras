#!/usr/bin/env python3
"""
Polymarket -> Coinbase signal pipeline.

Monitors Polymarket probability shifts and generates trading signals for
crypto symbols on Coinbase.  Polymarket candlestick data is already ingested
via websocket into the database (exchange_id=3, symbols prefixed with PM:).

Probability values are stored as OHLC prices in [0, 1] range.
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Keyword -> crypto symbol mapping ─────────────────────────────────────
# Each entry: keyword (matched case-insensitively against market name)
#   -> list of (crypto_symbol, correlation_weight)
# Positive weight = probability increase is bullish for the crypto.
# Negative weight = probability increase is bearish (e.g. rate hikes).

DEFAULT_MARKET_CRYPTO_MAP = {
    # Direct crypto price markets
    "bitcoin": [("BTC-USD", 1.0)],
    "btc": [("BTC-USD", 1.0)],
    "ethereum": [("ETH-USD", 1.0)],
    "eth": [("ETH-USD", 1.0)],
    "solana": [("SOL-USD", 1.0)],
    "sol": [("SOL-USD", 1.0)],
    "crypto": [("BTC-USD", 0.7), ("ETH-USD", 0.5)],

    # Macro events with crypto impact
    "fed rate": [("BTC-USD", -0.5), ("ETH-USD", -0.4)],
    "fed increase": [("BTC-USD", -0.6), ("ETH-USD", -0.5)],
    "fed decrease": [("BTC-USD", 0.6), ("ETH-USD", 0.5)],
    "interest rate": [("BTC-USD", -0.4), ("ETH-USD", -0.3)],
    "no change in fed": [("BTC-USD", 0.3), ("ETH-USD", 0.2)],  # status quo = mild positive
    "recession": [("BTC-USD", -0.4), ("ETH-USD", -0.4)],
    "tariff": [("BTC-USD", -0.3), ("ETH-USD", -0.3)],

    # Regulatory / political
    "etf approval": [("BTC-USD", 0.8), ("ETH-USD", 0.5)],
    "regulation": [("BTC-USD", -0.3), ("ETH-USD", -0.3)],
    "ban crypto": [("BTC-USD", -0.7), ("ETH-USD", -0.7)],
}

# Sectors that are potentially crypto-relevant (from securities.sector)
RELEVANT_SECTORS = {"crypto", "macro"}


class PolymarketSignalGenerator:
    """Generates Coinbase trading signals from Polymarket probability shifts."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        min_probability_delta: float = 0.05,
        market_crypto_map: Optional[Dict] = None,
        windows_hours: Optional[List[int]] = None,
    ):
        """
        Args:
            db_path: Path to crypto_data.db.
            min_probability_delta: Minimum absolute probability change to generate
                a signal (default 5%).
            market_crypto_map: Keyword -> [(symbol, weight)] mapping.  Falls back
                to DEFAULT_MARKET_CRYPTO_MAP.
            windows_hours: List of lookback windows to compute shifts over.
                Default: [1, 4, 24].
        """
        self.db_path = db_path
        self.min_probability_delta = min_probability_delta
        self.market_crypto_map = market_crypto_map or DEFAULT_MARKET_CRYPTO_MAP
        self.windows_hours = windows_hours or [1, 4, 24]

    # ── helpers ───────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── public API ────────────────────────────────────────────────────────

    def get_polymarket_symbols(self) -> List[dict]:
        """Return active Polymarket securities from the securities table.

        Each entry is a dict with keys: id, symbol, name, sector,
        expiry_date, metadata_json.
        """
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, symbol, name, sector, expiry_date, metadata_json "
            "FROM securities WHERE exchange_id = 3 AND is_active = 1 "
            "ORDER BY sector, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def compute_probability_shifts(self, hours: int = 4) -> List[dict]:
        """Compute probability changes for Polymarket markets over *hours*.

        Uses 1h candlesticks.  For each market the shift is computed as:
            latest_close - close_at_start_of_window

        Returns a list of dicts:
            symbol, name, sector, prob_now, prob_then, delta,
            hours, expiry_date
        sorted by absolute delta descending.
        """
        conn = self._connect()
        now_ts = int(datetime.now(timezone.utc).timestamp())
        window_start_ts = now_ts - hours * 3600

        # Get latest 1h close for each PM symbol
        query = """
            SELECT s.symbol, s.name, s.sector, s.expiry_date,
                   c_now.close AS prob_now,
                   c_old.close AS prob_then
            FROM securities s
            -- latest candle
            JOIN (
                SELECT symbol, close, timestamp
                FROM candlesticks
                WHERE symbol LIKE 'PM:%' AND timeframe = '1h'
                  AND timestamp <= ?
                GROUP BY symbol
                HAVING timestamp = MAX(timestamp)
            ) c_now ON c_now.symbol = s.symbol
            -- candle nearest to window start
            JOIN (
                SELECT symbol, close, timestamp
                FROM candlesticks
                WHERE symbol LIKE 'PM:%' AND timeframe = '1h'
                  AND timestamp <= ?
                GROUP BY symbol
                HAVING timestamp = MAX(timestamp)
            ) c_old ON c_old.symbol = s.symbol
            WHERE s.exchange_id = 3 AND s.is_active = 1
        """
        rows = conn.execute(query, (now_ts, window_start_ts)).fetchall()
        conn.close()

        results = []
        for r in rows:
            prob_now = r["prob_now"]
            prob_then = r["prob_then"]
            if prob_now is None or prob_then is None:
                continue
            delta = prob_now - prob_then
            results.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "sector": r["sector"],
                "prob_now": round(prob_now, 4),
                "prob_then": round(prob_then, 4),
                "delta": round(delta, 4),
                "hours": hours,
                "expiry_date": r["expiry_date"],
            })

        # Sort by absolute delta descending
        results.sort(key=lambda x: abs(x["delta"]), reverse=True)
        return results

    def _match_keywords(self, name: str) -> List[Tuple[str, float]]:
        """Match a market name against the keyword map.

        Returns deduplicated list of (crypto_symbol, weight) pairs.
        The weight already incorporates the keyword's correlation sign.
        """
        name_lower = name.lower()
        hits: Dict[str, float] = {}
        for keyword, mappings in self.market_crypto_map.items():
            if keyword.lower() in name_lower:
                for sym, weight in mappings:
                    # If multiple keywords match the same symbol, take the
                    # one with the largest absolute weight.
                    if sym not in hits or abs(weight) > abs(hits[sym]):
                        hits[sym] = weight
        return list(hits.items())

    def map_to_crypto_signals(self, shifts: List[dict]) -> List[dict]:
        """Convert probability shifts to crypto trading signals.

        Only shifts exceeding self.min_probability_delta are considered.

        Signal format:
            symbol        - Coinbase symbol to trade (e.g. BTC-USD)
            action        - BUY or SELL
            strength      - 0-100
            reason        - human-readable explanation
            source        - "polymarket"
            polymarket_symbol - the PM:* symbol
            probability_delta - raw delta
        """
        signals: List[dict] = []
        for shift in shifts:
            if abs(shift["delta"]) < self.min_probability_delta:
                continue

            # Try keyword matching on the market name
            mappings = self._match_keywords(shift["name"])
            if not mappings:
                # Also try matching on the symbol slug
                mappings = self._match_keywords(shift["symbol"].replace("-", " "))
            if not mappings:
                continue

            for crypto_sym, corr_weight in mappings:
                # Effective move: delta * correlation weight
                effective_delta = shift["delta"] * corr_weight
                # Positive effective_delta = bullish signal
                action = "BUY" if effective_delta > 0 else "SELL"

                # Strength: scale |effective_delta| from 0-1 into 0-100.
                # A 10% effective move = strength ~50; 20% = ~100.
                raw_strength = min(abs(effective_delta) / 0.20, 1.0) * 100
                # Floor at 20 (below that we wouldn't emit it anyway)
                strength = max(round(raw_strength, 1), 20)

                pct_now = shift["prob_now"] * 100
                pct_then = shift["prob_then"] * 100
                delta_pct = shift["delta"] * 100

                reason = (
                    f"Polymarket: '{shift['name']}' probability "
                    f"{'+' if delta_pct >= 0 else ''}{delta_pct:.1f}% "
                    f"({pct_then:.0f}%>{pct_now:.0f}%) "
                    f"in {shift['hours']}h "
                    f"[corr={corr_weight:+.1f}]"
                )

                signals.append({
                    "symbol": crypto_sym,
                    "action": action,
                    "strength": strength,
                    "reason": reason,
                    "source": "polymarket",
                    "polymarket_symbol": shift["symbol"],
                    "probability_delta": round(shift["delta"], 4),
                })

        # Deduplicate: if multiple PM markets produce the same (symbol, action),
        # keep the strongest signal.
        best: Dict[Tuple[str, str], dict] = {}
        for sig in signals:
            key = (sig["symbol"], sig["action"])
            if key not in best or sig["strength"] > best[key]["strength"]:
                best[key] = sig
        return sorted(best.values(), key=lambda s: s["strength"], reverse=True)

    def generate_signals(self) -> List[dict]:
        """Main entry point: compute shifts across all windows, map to signals.

        Returns a merged, deduplicated list of signals (strongest per
        symbol+action across all windows).
        """
        all_signals: List[dict] = []
        for hours in self.windows_hours:
            shifts = self.compute_probability_shifts(hours=hours)
            mapped = self.map_to_crypto_signals(shifts)
            all_signals.extend(mapped)

        # Final dedup across windows: keep strongest per (symbol, action)
        best: Dict[Tuple[str, str], dict] = {}
        for sig in all_signals:
            key = (sig["symbol"], sig["action"])
            if key not in best or sig["strength"] > best[key]["strength"]:
                best[key] = sig
        return sorted(best.values(), key=lambda s: s["strength"], reverse=True)


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Polymarket -> Coinbase signal pipeline"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.05,
        help="Min probability delta to generate a signal (default: 0.05 = 5%%)",
    )
    parser.add_argument(
        "--window", type=int, default=None,
        help="Single lookback window in hours (default: scan 1h, 4h, 24h)",
    )
    parser.add_argument(
        "--all-shifts", action="store_true",
        help="Show all probability shifts, not just crypto-relevant ones",
    )
    args = parser.parse_args()

    gen = PolymarketSignalGenerator(min_probability_delta=args.threshold)
    if args.window:
        gen.windows_hours = [args.window]

    # ── Show active Polymarket markets ────────────────────────────────
    markets = gen.get_polymarket_symbols()
    crypto_macro = [m for m in markets if m["sector"] in RELEVANT_SECTORS]
    print(f"Polymarket markets: {len(markets)} total, "
          f"{len(crypto_macro)} crypto/macro-relevant\n")

    # ── Probability shifts ────────────────────────────────────────────
    for hours in gen.windows_hours:
        shifts = gen.compute_probability_shifts(hours=hours)
        relevant = [s for s in shifts if s["sector"] in RELEVANT_SECTORS]
        display = shifts if args.all_shifts else relevant

        print(f"--- Probability shifts ({hours}h window) "
              f"[{len(relevant)} relevant / {len(shifts)} total] ---")

        if not display:
            print("  (none)\n")
            continue

        for s in display[:20]:
            delta_pct = s["delta"] * 100
            arrow = "+" if delta_pct >= 0 else ""
            sector_tag = f"[{s['sector']}]" if s["sector"] else ""
            print(f"  {arrow}{delta_pct:5.1f}%  "
                  f"{s['prob_now']*100:5.1f}%  "
                  f"{s['name'][:60]:<60s} {sector_tag}")
        if len(display) > 20:
            print(f"  ... and {len(display) - 20} more")
        print()

    # ── Generated signals ─────────────────────────────────────────────
    signals = gen.generate_signals()
    print(f"=== Generated signals ({len(signals)}) ===")
    if not signals:
        print("  No actionable signals (probability shifts below threshold "
              f"or no crypto-relevant markets moved).")
    else:
        for sig in signals:
            print(f"\n  {sig['action']:4s} {sig['symbol']:<10s} "
                  f"strength={sig['strength']:.0f}")
            print(f"       {sig['reason']}")
            print(f"       pm_symbol={sig['polymarket_symbol']}  "
                  f"delta={sig['probability_delta']:+.2%}")


if __name__ == "__main__":
    main()
