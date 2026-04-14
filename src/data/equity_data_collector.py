#!/usr/bin/env python3
"""
Equity data collector via yfinance.
Fetches OHLCV data for equity watchlist and index symbols,
stores in the same crypto_data.db schema for unified analysis.
Includes market hours awareness for intraday data.
"""

import os
import sys
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    EQUITY_SYMBOLS,
    INDEX_SYMBOLS,
    MIN_DAYS_FOR_INDICATORS,
)
from data.indicator_calculator import calculate_all_indicators, INDICATOR_COLUMNS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:
    logger.error("yfinance not installed. Run: pip install yfinance")
    sys.exit(1)


class EquityDataCollector:
    """Collect and store equity / index data using yfinance."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure candlesticks and indicators tables exist (same schema as crypto)."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Tables should already exist from crypto pipeline; create if not
        cur.execute("""
            CREATE TABLE IF NOT EXISTS candlesticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, timestamp)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                sma_20 REAL, sma_50 REAL, sma_200 REAL,
                ema_12 REAL, ema_26 REAL,
                rsi_14 REAL,
                macd_line REAL, macd_signal REAL, macd_histogram REAL,
                bb_upper REAL, bb_middle REAL, bb_lower REAL, bb_width REAL,
                atr_14 REAL,
                volume_sma_20 REAL, volume_ratio REAL,
                adx_14 REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timeframe, timestamp)
            )
        """)
        conn.commit()
        conn.close()

    # ── Market hours ─────────────────────────────────────────────────────

    @staticmethod
    def is_us_market_open() -> bool:
        """Check if US equity markets are open (9:30 AM - 4 PM ET, weekdays)."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now_et <= market_close

    # ── Data fetching ────────────────────────────────────────────────────

    def fetch_historical(
        self,
        symbol: str,
        period: str = "2y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch historical OHLCV from yfinance."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                logger.warning(f"No data returned for {symbol} ({interval})")
                return pd.DataFrame()

            df = df.reset_index()
            # Normalize column names
            df.columns = [c.lower() for c in df.columns]
            if "date" in df.columns:
                df["timestamp"] = df["date"].apply(lambda d: int(d.timestamp()))
            elif "datetime" in df.columns:
                df["timestamp"] = df["datetime"].apply(lambda d: int(d.timestamp()))
            else:
                logger.warning(f"No date column for {symbol}")
                return pd.DataFrame()

            # Keep only OHLCV
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    logger.warning(f"Missing column {col} for {symbol}")
                    return pd.DataFrame()

            return df[["timestamp", "open", "high", "low", "close", "volume"]]

        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return pd.DataFrame()

    def _yf_interval_to_timeframe(self, interval: str) -> str:
        """Map yfinance interval to our timeframe labels."""
        mapping = {"1h": "1h", "1d": "1d", "5d": "1d"}
        return mapping.get(interval, "1d")

    # ── Saving ───────────────────────────────────────────────────────────

    def save_candles(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        """Save OHLCV data to candlesticks table using batch insert."""
        if df.empty:
            return 0

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        rows = [
            (
                symbol,
                timeframe,
                int(row["timestamp"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            for _, row in df.iterrows()
        ]
        cur.executemany(
            "INSERT OR IGNORE INTO candlesticks "
            "(symbol, timeframe, timestamp, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        saved = cur.rowcount

        conn.commit()
        conn.close()
        if saved:
            logger.info(f"Saved {saved} candles for {symbol}/{timeframe}")
        return saved

    # ── Indicator calculation ────────────────────────────────────────────

    def calculate_indicators(self, symbol: str, timeframe: str):
        """Compute indicators for an equity symbol using the shared calculator with batch insert."""
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candlesticks WHERE symbol=? AND timeframe=? ORDER BY timestamp",
            conn,
            params=(symbol, timeframe),
        )

        if len(df) < MIN_DAYS_FOR_INDICATORS:
            logger.debug(f"Skip indicators {symbol}/{timeframe}: only {len(df)} rows")
            conn.close()
            return

        df = calculate_all_indicators(df)

        rows = []
        for _, row in df.iterrows():
            if pd.isna(row.get("sma_20")):
                continue
            vals = [
                float(row[col]) if not pd.isna(row.get(col)) else None for col in INDICATOR_COLUMNS
            ]
            rows.append((symbol, timeframe, int(row["timestamp"]), *vals))

        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO indicators "
                "(symbol, timeframe, timestamp, "
                + ", ".join(INDICATOR_COLUMNS)
                + ") VALUES (?,?,?,"
                + ",".join(["?"] * len(INDICATOR_COLUMNS))
                + ")",
                rows,
            )
            conn.commit()
            logger.info(f"Indicators computed: {symbol}/{timeframe} ({len(rows)} rows)")

        conn.close()

    # ── 4h aggregation ───────────────────────────────────────────────────

    def aggregate_4h_candles(self, symbol: str, lookback_days: int = 30) -> int:
        """Build 4h candles for equities by aggregating 1h candles at UTC boundaries.

        Uses the same 4h UTC boundaries as the crypto pipeline (00,04,08,12,16,20).
        Equity markets only trade ~6.5 hours/day, so each trading day produces
        2 UTC 4h buckets each containing 1-4 hourly bars.
        Uses min_count=1 (vs crypto's exactly-4 requirement) since equity
        1h bars only exist during market hours.

        Returns the number of new/updated 4h candles written.
        """
        conn = sqlite3.connect(self.db_path)
        cutoff_ts = int((datetime.utcnow() - timedelta(days=lookback_days)).timestamp())

        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candlesticks WHERE symbol=? AND timeframe='1h' AND timestamp>=? "
            "ORDER BY timestamp",
            conn,
            params=(symbol, cutoff_ts),
        )
        if df.empty:
            conn.close()
            return 0

        # Assign each 1h candle to its 4h UTC bucket
        df["bucket"] = (df["timestamp"] // (4 * 3600)) * (4 * 3600)

        agg = (
            df.groupby("bucket")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
                count=("timestamp", "count"),
            )
            .reset_index()
        )

        # For equities: accept any bucket with at least 1 hourly bar.
        # (Equity markets are not 24/7 so we can't require exactly 4 bars.)
        agg = agg[agg["count"] >= 1]

        inserted = 0
        cursor = conn.cursor()
        for _, row in agg.iterrows():
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO candlesticks "
                    "(symbol, timeframe, timestamp, open, high, low, close, volume) "
                    "VALUES (?, '4h', ?, ?, ?, ?, ?, ?)",
                    (
                        symbol,
                        int(row["bucket"]),
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["volume"],
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.error(f"Error inserting equity 4h candle for {symbol}: {e}")

        conn.commit()
        conn.close()

        if inserted > 0:
            logger.info(f"Aggregated {inserted} equity 4h candles for {symbol}")
        return inserted

    # ── Orchestration ────────────────────────────────────────────────────

    def collect_all(
        self,
        symbols: List[str] = None,
        period: str = "2y",
        include_intraday: bool = True,
    ):
        """Collect data for all equity + index symbols."""
        if symbols is None:
            symbols = list(set(EQUITY_SYMBOLS + INDEX_SYMBOLS))

        logger.info(f"Collecting {len(symbols)} equity/index symbols")

        for symbol in symbols:
            # Daily data (always)
            logger.info(f"Fetching daily: {symbol}")
            df = self.fetch_historical(symbol, period=period, interval="1d")
            self.save_candles(symbol, "1d", df)
            self.calculate_indicators(symbol, "1d")

            # Intraday (1h) — only if market is open or we want historical
            if include_intraday:
                logger.info(f"Fetching 1h: {symbol}")
                # yfinance allows max ~730 days for 1h on most symbols
                df_1h = self.fetch_historical(symbol, period="2y", interval="1h")
                self.save_candles(symbol, "1h", df_1h)
                self.calculate_indicators(symbol, "1h")

                # Aggregate 4h from 1h data (30-day lookback for full rebuild)
                logger.info(f"Aggregating 4h: {symbol}")
                self.aggregate_4h_candles(symbol, lookback_days=30)
                self.calculate_indicators(symbol, "4h")

            time.sleep(0.5)  # be nice to Yahoo Finance

        logger.info("Equity collection complete")

    def update_latest(self, symbols: List[str] = None):
        """Fetch only recent data (last 5 days) for incremental updates."""
        if symbols is None:
            symbols = list(set(EQUITY_SYMBOLS + INDEX_SYMBOLS))

        for symbol in symbols:
            df = self.fetch_historical(symbol, period="5d", interval="1d")
            self.save_candles(symbol, "1d", df)
            self.calculate_indicators(symbol, "1d")

            if self.is_us_market_open():
                df_1h = self.fetch_historical(symbol, period="5d", interval="1h")
                self.save_candles(symbol, "1h", df_1h)
                self.calculate_indicators(symbol, "1h")

            # Always aggregate 4h (uses existing 1h data, even outside market hours)
            self.aggregate_4h_candles(symbol, lookback_days=14)
            self.calculate_indicators(symbol, "4h")

            time.sleep(0.3)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Equity Data Collector")
    parser.add_argument("--collect", action="store_true", help="Full historical collection")
    parser.add_argument("--update", action="store_true", help="Update latest data")
    parser.add_argument("--symbol", type=str, help="Single symbol")
    parser.add_argument("--period", default="2y", help="yfinance period (default 2y)")
    parser.add_argument("--validate", action="store_true", help="Show data coverage")
    args = parser.parse_args()

    collector = EquityDataCollector()

    if args.collect:
        symbols = [args.symbol] if args.symbol else None
        collector.collect_all(symbols=symbols, period=args.period)
    elif args.update:
        symbols = [args.symbol] if args.symbol else None
        collector.update_latest(symbols=symbols)
    elif args.validate:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        all_eq = list(set(EQUITY_SYMBOLS + INDEX_SYMBOLS))
        print(f"\n{'Symbol':<10} {'TF':<4} {'Candles':>8} {'First':>12} {'Last':>12}")
        print("-" * 50)
        for sym in sorted(all_eq):
            for tf in ["1d", "4h", "1h"]:
                cur.execute(
                    "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                    "FROM candlesticks WHERE symbol=? AND timeframe=?",
                    (sym, tf),
                )
                cnt, mn, mx = cur.fetchone()
                if cnt > 0:
                    first = datetime.utcfromtimestamp(mn).strftime("%Y-%m-%d")
                    last = datetime.utcfromtimestamp(mx).strftime("%Y-%m-%d")
                else:
                    first = last = "N/A"
                print(f"{sym:<10} {tf:<4} {cnt:>8} {first:>12} {last:>12}")
        conn.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
