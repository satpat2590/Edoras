#!/usr/bin/env python3
"""
Intra‑day data update for portfolio management.
Lightweight script that fetches latest 1‑hour data for portfolio symbols only.
Runs every 2 hours during market hours (10:30 AM‑6:30 PM EDT).
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
import time
from typing import List, Dict
import json

# Try to import pandas
try:
    import pandas as pd

    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("pandas not installed. Some functionality will be limited.")

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import Coinbase client
try:
    from coinbase.rest import RESTClient
except ImportError:
    logger.error("coinbase-advanced-py not installed. Run: pip install coinbase-advanced-py")
    sys.exit(1)

try:
    from config import DB_PATH as _CONFIG_DB_PATH
except ImportError:
    _CONFIG_DB_PATH = "crypto_data.db"


class IntradayUpdater:
    """Lightweight intra‑day data updater"""

    # Hardcoded portfolio symbols (verified supported on Coinbase)
    PORTFOLIO_SYMBOLS = [
        "ETH-USD",
        "BTC-USD",
        "XRP-USD",
        "TROLL-USD",
        "BONK-USD",
        "FET-USD",
        "AMP-USD",
        "GRT-USD",
    ]

    def __init__(self, db_path: str = _CONFIG_DB_PATH):
        """Initialize updater with database and API credentials"""
        self.db_path = db_path

        # Load credentials from environment
        self.api_key = os.getenv("COINBASE_API_KEY")
        self.api_secret = os.getenv("COINBASE_API_SECRET")

        if not self.api_key or not self.api_secret:
            logger.error("COINBASE_API_KEY and COINBASE_API_SECRET environment variables not set")
            sys.exit(1)

        # Strip surrounding quotes if present
        if self.api_key.startswith('"') and self.api_key.endswith('"'):
            self.api_key = self.api_key[1:-1]
        if self.api_secret.startswith('"') and self.api_secret.endswith('"'):
            self.api_secret = self.api_secret[1:-1]

        # Fix newlines in EC private key
        if "-----BEGIN EC PRIVATE KEY-----" in self.api_secret:
            self.api_secret = self.api_secret.replace("\\n", "\n")

        # Initialize Coinbase client
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)

        logger.info("Intraday updater initialized")

    def fetch_latest_candles(self, symbol: str, lookback_hours: int = 48) -> List[Dict]:
        """Fetch latest 1‑hour candles for a symbol"""
        end_time = int(time.time())
        start_time = end_time - (lookback_hours * 3600)

        try:
            candles = self.client.get_candles(
                product_id=symbol, start=str(start_time), end=str(end_time), granularity="ONE_HOUR"
            )

            if hasattr(candles, "candles"):
                data = []
                for candle in candles.candles:
                    data.append(
                        {
                            "timestamp": getattr(candle, "start", 0),
                            "open": float(getattr(candle, "open", 0)),
                            "high": float(getattr(candle, "high", 0)),
                            "low": float(getattr(candle, "low", 0)),
                            "close": float(getattr(candle, "close", 0)),
                            "volume": float(getattr(candle, "volume", 0)),
                        }
                    )
                logger.debug(f"Fetched {len(data)} candles for {symbol}")
                return data
            else:
                logger.warning(f"No candles returned for {symbol}")
                return []

        except Exception as e:
            if "INVALID_ARGUMENT" in str(e) or "ProductID is invalid" in str(e):
                logger.error(f"Symbol {symbol} not supported: {e}")
                return []
            else:
                logger.error(f"Error fetching candles for {symbol}: {e}")
                return []

    def update_candlestick_data(self, symbol: str):
        """Update candlestick data for a symbol (1h timeframe only)"""
        candles = self.fetch_latest_candles(symbol, lookback_hours=48)
        if not candles:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        inserted = 0
        for candle in candles:
            try:
                cursor.execute(
                    """
                INSERT OR IGNORE INTO candlesticks 
                (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, '1h', ?, ?, ?, ?, ?, ?)
                """,
                    (
                        symbol,
                        candle["timestamp"],
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.error(f"Error inserting candlestick for {symbol}: {e}")

        conn.commit()
        conn.close()

        if inserted > 0:
            logger.info(f"Updated {symbol}: {inserted} new candles")

        # Calculate indicators if we have enough data
        self.calculate_indicators(symbol, "1h")

    def calculate_indicators(self, symbol: str, timeframe: str):
        """Calculate technical indicators for a symbol/timeframe using batch insert"""
        conn = sqlite3.connect(self.db_path)

        try:
            query = """
            SELECT timestamp, open, high, low, close, volume
            FROM candlesticks
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp
            """

            df = pd.read_sql_query(query, conn, params=(symbol, timeframe))

            min_rows = {"1h": 50, "4h": 30, "1d": 10}.get(timeframe, 50)
            if len(df) < min_rows:
                logger.warning(
                    f"Not enough data for indicators: {symbol} {timeframe} ({len(df)} rows, need {min_rows})"
                )
                return

            from data.indicator_calculator import calculate_all_indicators, INDICATOR_COLUMNS

            df = calculate_all_indicators(df)

            rows = []
            for _, row in df.iterrows():
                if pd.isna(row.get("sma_20")):
                    continue
                vals = [
                    float(row[col]) if not pd.isna(row.get(col)) else None
                    for col in INDICATOR_COLUMNS
                ]
                rows.append((symbol, timeframe, int(row["timestamp"]), *vals))

            if not rows:
                return

            cursor = conn.cursor()
            cursor.executemany(
                "INSERT OR REPLACE INTO indicators "
                f"(symbol, timeframe, timestamp, {', '.join(INDICATOR_COLUMNS)}) "
                f"VALUES (?,?,?,{','.join(['?'] * len(INDICATOR_COLUMNS))})",
                rows,
            )
            conn.commit()

            logger.info(f"Calculated indicators for {symbol} {timeframe}")

        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol} {timeframe}: {e}")
        finally:
            conn.close()

    # NOTE: Local calculate_rsi / calculate_adx / true_range removed.
    # All indicator computation now goes through indicator_calculator.calculate_all_indicators().

    def check_signals(self, symbol: str):
        """Check for immediate trading signals"""
        conn = sqlite3.connect(self.db_path)

        try:
            # Get latest indicators
            query = """
            SELECT i.timestamp, i.rsi_14, i.macd_histogram, c.close
            FROM indicators i
            JOIN candlesticks c ON i.symbol = c.symbol 
                AND i.timeframe = c.timeframe 
                AND i.timestamp = c.timestamp
            WHERE i.symbol = ? AND i.timeframe = '1h'
            ORDER BY i.timestamp DESC
            LIMIT 1
            """

            cursor = conn.cursor()
            cursor.execute(query, (symbol,))
            row = cursor.fetchone()

            if not row:
                return None

            timestamp, rsi, macd_hist, price = row

            signals = []

            # RSI signals
            if rsi is not None:
                if rsi < 30:
                    signals.append(("RSI_OVERSOLD", f"RSI {rsi:.1f} < 30"))
                elif rsi > 70:
                    signals.append(("RSI_OVERBOUGHT", f"RSI {rsi:.1f} > 70"))

            # MACD signals
            if macd_hist is not None:
                prev_query = """
                SELECT macd_histogram FROM indicators
                WHERE symbol = ? AND timeframe = '1h'
                ORDER BY timestamp DESC
                LIMIT 2
                """
                cursor.execute(prev_query, (symbol,))
                rows = cursor.fetchall()
                if len(rows) == 2:
                    prev_hist = rows[1][0]
                    if prev_hist is not None and macd_hist is not None:
                        if prev_hist < 0 and macd_hist > 0:
                            signals.append(("MACD_BULLISH_CROSS", "MACD crossed above signal"))
                        elif prev_hist > 0 and macd_hist < 0:
                            signals.append(("MACD_BEARISH_CROSS", "MACD crossed below signal"))

            return signals if signals else None

        except Exception as e:
            logger.error(f"Error checking signals for {symbol}: {e}")
            return None
        finally:
            conn.close()

    def run_update(self):
        """Run full intra‑day update — gap-filler for WebSocket data.

        Checks if WebSocket has recent data (within 2 hours). If so, skips
        REST fetch entirely. Only fetches missing candles via REST when gaps exist.
        """
        logger.info("Starting intra‑day update (gap-filler mode)")
        start_time = time.time()

        signals_found = []
        gap_threshold = 7200  # 2 hours — if WS data is fresher than this, skip REST

        for symbol in self.PORTFOLIO_SYMBOLS:
            logger.info(f"Checking {symbol}...")

            # Check if we have recent 1h data (from WebSocket)
            conn = sqlite3.connect(self.db_path)
            last_ts = conn.execute(
                "SELECT MAX(timestamp) FROM candlesticks WHERE symbol=? AND timeframe='1h'",
                (symbol,),
            ).fetchone()[0]
            conn.close()

            now_ts = int(time.time())
            if last_ts and (now_ts - last_ts) < gap_threshold:
                # WebSocket has recent data — skip REST fetch, just check signals
                logger.info(
                    f"{symbol}: recent data exists (age={now_ts - last_ts}s), skipping REST fetch"
                )
            else:
                # Gap detected — fetch missing candles via REST
                age = now_ts - last_ts if last_ts else "no data"
                logger.info(f"{symbol}: gap detected (last data: {age}s ago), fetching via REST")
                self.update_candlestick_data(symbol)

            # Check for signals (always)
            signals = self.check_signals(symbol)
            if signals:
                for signal_type, description in signals:
                    signals_found.append(
                        {
                            "symbol": symbol,
                            "type": signal_type,
                            "description": description,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )

        # Aggregate 1h → 4h candles and compute 4h indicators (only if needed)
        try:
            from data.crypto_data_collector import CryptoDataCollector

            collector = CryptoDataCollector(db_path=self.db_path)
            for symbol in self.PORTFOLIO_SYMBOLS:
                # Check if 4h data is recent
                conn = sqlite3.connect(self.db_path)
                last_4h = conn.execute(
                    "SELECT MAX(timestamp) FROM candlesticks WHERE symbol=? AND timeframe='4h'",
                    (symbol,),
                ).fetchone()[0]
                conn.close()

                if last_4h and (now_ts - last_4h) < gap_threshold:
                    logger.info(f"{symbol}: 4h data recent, skipping aggregation")
                    continue

                collector.aggregate_4h_candles(symbol, lookback_days=7)
                self.calculate_indicators(symbol, "4h")
            logger.info("4h candle aggregation + indicators complete")
        except Exception as e:
            logger.warning(f"4h aggregation failed: {e}")

        elapsed = time.time() - start_time
        logger.info(f"Intra‑day update completed in {elapsed:.1f} seconds")

        # Log signals found
        if signals_found:
            logger.info(f"Found {len(signals_found)} signals:")
            for signal in signals_found:
                logger.info(f"  {signal['symbol']}: {signal['type']} - {signal['description']}")

        return signals_found, elapsed


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description="Intra‑day Data Updater")
    parser.add_argument("--test", action="store_true", help="Test mode (single symbol)")
    parser.add_argument("--symbol", type=str, help="Specific symbol to update")

    args = parser.parse_args()

    # Check for pandas availability
    if not PANDAS_AVAILABLE:
        logger.error("pandas not installed. Run: pip install pandas")
        sys.exit(1)

    updater = IntradayUpdater(_CONFIG_DB_PATH)

    if args.test or args.symbol:
        symbol = args.symbol or updater.PORTFOLIO_SYMBOLS[0]
        logger.info(f"Testing with {symbol}")

        # Update single symbol
        updater.update_candlestick_data(symbol)

        # Check signals
        signals = updater.check_signals(symbol)
        if signals:
            print(f"Signals for {symbol}:")
            for signal_type, description in signals:
                print(f"  • {signal_type}: {description}")
        else:
            print(f"No signals for {symbol}")
    else:
        # Run full update
        signals, elapsed = updater.run_update()

        # Log signals summary (Telegram alerts disabled)
        if signals:
            logger.info(f"Telegram alerts disabled — {len(signals)} intraday signals logged only")
            for signal in signals[:5]:
                logger.info(f"  {signal['symbol']}: {signal['type']}")
            if len(signals) > 5:
                logger.info(f"  ... and {len(signals) - 5} more")


if __name__ == "__main__":
    main()
