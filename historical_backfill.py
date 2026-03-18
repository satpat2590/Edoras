#!/usr/bin/env python3
"""
Historical data backfill for Coinbase crypto data.
Fetches 400+ days of OHLCV data for all tracked symbols and recalculates indicators.
Handles rate limiting, resumption, and data quality validation.
"""

import os
import sys
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH, COINBASE_MAX_RPS, COINBASE_MAX_CANDLES,
    PORTFOLIO_SYMBOLS, TOP_CRYPTO_SYMBOLS,
    TIMEFRAMES, DEFAULT_BACKFILL_DAYS, MIN_DAYS_FOR_SMA200,
)
from indicator_calculator import calculate_all_indicators, INDICATOR_COLUMNS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, max_rps: int = 8):
        self.max_rps = max_rps
        self.interval = 1.0 / max_rps
        self.last_call = 0.0

    def wait(self):
        now = time.monotonic()
        elapsed = now - self.last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_call = time.monotonic()


class HistoricalBackfiller:
    """Orchestrates historical data backfill from Coinbase."""

    def __init__(self, db_path: str = DB_PATH, api_key: str = None, api_secret: str = None):
        self.db_path = db_path
        self.api_key = api_key or os.getenv("COINBASE_API_KEY")
        self.api_secret = api_secret or os.getenv("COINBASE_API_SECRET")

        if not self.api_key or not self.api_secret:
            raise ValueError("COINBASE_API_KEY and COINBASE_API_SECRET must be set")

        # Fix EC key newlines
        if self.api_secret and "-----BEGIN EC PRIVATE KEY-----" in self.api_secret:
            self.api_secret = self.api_secret.replace("\\n", "\n")

        from coinbase.rest import RESTClient
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
        self.limiter = RateLimiter(COINBASE_MAX_RPS)

        # Map timeframe labels to seconds-per-candle for chunking
        self._tf_seconds = {"1h": 3600, "4h": 21600, "1d": 86400}

    # ── Fetching ─────────────────────────────────────────────────────────

    def _fetch_candles(self, symbol: str, timeframe: str, start: int, end: int) -> List[Dict]:
        """Fetch a single chunk of candles from Coinbase."""
        granularity = TIMEFRAMES.get(timeframe, "ONE_DAY")
        self.limiter.wait()
        try:
            resp = self.client.get_candles(
                product_id=symbol,
                start=str(start),
                end=str(end),
                granularity=granularity,
            )
            if hasattr(resp, "candles"):
                return [
                    {
                        "timestamp": getattr(c, "start", 0),
                        "open": float(getattr(c, "open", 0)),
                        "high": float(getattr(c, "high", 0)),
                        "low": float(getattr(c, "low", 0)),
                        "close": float(getattr(c, "close", 0)),
                        "volume": float(getattr(c, "volume", 0)),
                    }
                    for c in resp.candles
                ]
        except Exception as e:
            logger.warning(f"Fetch error {symbol} {timeframe} [{start}-{end}]: {e}")
        return []

    def _save_candles(self, symbol: str, timeframe: str, candles: List[Dict]) -> int:
        """Insert candles into DB, returning count of new rows."""
        if not candles:
            return 0
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        saved = 0
        for c in candles:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (symbol, timeframe, c["timestamp"], c["open"], c["high"], c["low"], c["close"], c["volume"]),
                )
                if cur.rowcount > 0:
                    saved += 1
            except Exception as e:
                logger.warning(f"Save error {symbol}: {e}")
        conn.commit()
        conn.close()
        return saved

    # ── Per-symbol backfill ──────────────────────────────────────────────

    def backfill_symbol(self, symbol: str, timeframe: str, days_back: int = DEFAULT_BACKFILL_DAYS) -> int:
        """Backfill a single symbol/timeframe with proper chunking and no early exit."""
        secs_per_candle = self._tf_seconds.get(timeframe, 86400)
        chunk_candles = COINBASE_MAX_CANDLES  # 300
        chunk_seconds = chunk_candles * secs_per_candle

        end_ts = int(time.time())
        start_ts = end_ts - (days_back * 86400)

        total_saved = 0
        current = start_ts

        while current < end_ts:
            chunk_end = min(current + chunk_seconds, end_ts)
            candles = self._fetch_candles(symbol, timeframe, current, chunk_end)
            saved = self._save_candles(symbol, timeframe, candles)
            total_saved += saved

            # Always advance; never break early (fixes original bug)
            current = chunk_end

        return total_saved

    # ── Full backfill orchestration ──────────────────────────────────────

    def backfill_all(self, days_back: int = DEFAULT_BACKFILL_DAYS, symbols: List[str] = None):
        """Backfill all symbols across all timeframes."""
        if symbols is None:
            seen = set()
            symbols = []
            for s in PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS:
                if s not in seen:
                    seen.add(s)
                    symbols.append(s)

        timeframes = list(TIMEFRAMES.keys())
        total_tasks = len(symbols) * len(timeframes)
        done = 0

        logger.info(f"Backfilling {len(symbols)} symbols x {len(timeframes)} timeframes = {total_tasks} tasks")
        logger.info(f"Lookback: {days_back} days")

        for symbol in symbols:
            for tf in timeframes:
                done += 1
                logger.info(f"[{done}/{total_tasks}] {symbol} {tf}")
                saved = self.backfill_symbol(symbol, tf, days_back)
                if saved > 0:
                    logger.info(f"  → {saved} new candles")

        logger.info("Backfill complete. Recalculating indicators...")
        self.recalculate_all_indicators(symbols)

    # ── Indicator recalculation ──────────────────────────────────────────

    def recalculate_all_indicators(self, symbols: List[str] = None):
        """Recalculate indicators for all symbol/timeframe pairs using shared module."""
        conn = sqlite3.connect(self.db_path)

        if symbols is None:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT symbol FROM candlesticks")
            symbols = [r[0] for r in cur.fetchall()]

        timeframes = list(TIMEFRAMES.keys())

        for symbol in symbols:
            for tf in timeframes:
                query = (
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candlesticks WHERE symbol=? AND timeframe=? ORDER BY timestamp"
                )
                df = pd.read_sql_query(query, conn, params=(symbol, tf))

                if len(df) < 50:
                    logger.debug(f"Skip indicators {symbol}/{tf}: only {len(df)} rows")
                    continue

                df = calculate_all_indicators(df)

                # Upsert indicators
                cur = conn.cursor()
                for _, row in df.iterrows():
                    if pd.isna(row.get("sma_20")):
                        continue
                    vals = [
                        float(row[col]) if not pd.isna(row.get(col)) else None
                        for col in INDICATOR_COLUMNS
                    ]
                    cur.execute(
                        "INSERT OR REPLACE INTO indicators "
                        "(symbol, timeframe, timestamp, "
                        + ", ".join(INDICATOR_COLUMNS)
                        + ") VALUES (?,?,?," + ",".join(["?"] * len(INDICATOR_COLUMNS)) + ")",
                        (symbol, tf, int(row["timestamp"]), *vals),
                    )
                conn.commit()
                logger.info(f"Indicators updated: {symbol}/{tf}")

        conn.close()

    # ── Data quality validation ──────────────────────────────────────────

    def validate(self, symbols: List[str] = None) -> Dict:
        """Validate data coverage and quality."""
        conn = sqlite3.connect(self.db_path)

        if symbols is None:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT symbol FROM candlesticks")
            symbols = [r[0] for r in cur.fetchall()]

        report = {}
        for symbol in symbols:
            sym_report = {}
            for tf in TIMEFRAMES:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                    "FROM candlesticks WHERE symbol=? AND timeframe=?",
                    (symbol, tf),
                )
                count, min_ts, max_ts = cur.fetchone()
                days_covered = 0
                if min_ts and max_ts:
                    days_covered = (max_ts - min_ts) / 86400

                # Check SMA-200 availability
                cur.execute(
                    "SELECT COUNT(*) FROM indicators WHERE symbol=? AND timeframe=? AND sma_200 IS NOT NULL",
                    (symbol, tf),
                )
                sma200_count = cur.fetchone()[0]

                sym_report[tf] = {
                    "candles": count,
                    "days_covered": round(days_covered, 1),
                    "sma200_available": sma200_count > 0,
                    "sufficient_for_risk": count >= 30,
                }
            report[symbol] = sym_report

        conn.close()
        return report

    def print_validation(self):
        """Pretty-print data quality report."""
        report = self.validate()
        print(f"\n{'Symbol':<12} {'TF':<4} {'Candles':>8} {'Days':>7} {'SMA200':>7} {'Risk OK':>8}")
        print("-" * 52)
        for symbol, tfs in sorted(report.items()):
            for tf, info in sorted(tfs.items()):
                sma = "Yes" if info["sma200_available"] else "No"
                risk = "Yes" if info["sufficient_for_risk"] else "No"
                print(f"{symbol:<12} {tf:<4} {info['candles']:>8} {info['days_covered']:>7} {sma:>7} {risk:>8}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Historical Data Backfill")
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS, help="Days to backfill")
    parser.add_argument("--symbol", type=str, help="Single symbol to backfill")
    parser.add_argument("--timeframe", type=str, default=None, help="Single timeframe")
    parser.add_argument("--validate", action="store_true", help="Validate data coverage")
    parser.add_argument("--indicators-only", action="store_true", help="Recalculate indicators only")
    args = parser.parse_args()

    backfiller = HistoricalBackfiller()

    if args.validate:
        backfiller.print_validation()
    elif args.indicators_only:
        symbols = [args.symbol] if args.symbol else None
        backfiller.recalculate_all_indicators(symbols)
    elif args.symbol and args.timeframe:
        saved = backfiller.backfill_symbol(args.symbol, args.timeframe, args.days)
        print(f"Backfilled {saved} candles for {args.symbol}/{args.timeframe}")
        backfiller.recalculate_all_indicators([args.symbol])
    else:
        symbols = [args.symbol] if args.symbol else None
        backfiller.backfill_all(days_back=args.days, symbols=symbols)
        backfiller.print_validation()


if __name__ == "__main__":
    main()
