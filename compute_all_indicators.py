#!/usr/bin/env python3
"""
Compute technical indicators for all timeframes (1h, 4h, 1d) for portfolio symbols.
Uses the canonical indicator_calculator module (with corrected ADX, Bollinger Bands, etc).
"""
import os
import sys
import sqlite3
import logging

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from indicator_calculator import calculate_all_indicators, INDICATOR_COLUMNS
from config import PORTFOLIO_SYMBOLS, TOP_CRYPTO_SYMBOLS


def compute_all_indicators(db_path: str = 'crypto_data.db'):
    """Compute indicators for 1h, 4h, 1d timeframes using the canonical calculator."""
    # Include DEX tokens that have candlestick data
    dex_symbols = []
    try:
        _conn = sqlite3.connect(db_path)
        dex_symbols = [r[0] for r in _conn.execute(
            "SELECT symbol FROM securities WHERE is_dex = 1 AND is_active = 1"
        ).fetchall()]
        _conn.close()
    except Exception:
        pass
    symbols = list(dict.fromkeys(PORTFOLIO_SYMBOLS + TOP_CRYPTO_SYMBOLS + dex_symbols))
    timeframes = ['1h', '4h', '1d']

    conn = sqlite3.connect(db_path)

    for symbol in symbols:
        for tf in timeframes:
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM candlesticks WHERE symbol=? AND timeframe=?', (symbol, tf))
            candle_count = cur.fetchone()[0]

            if candle_count < 30:
                logger.warning(f"  Skipping {symbol} {tf}: insufficient candles ({candle_count})")
                continue

            logger.info(f"Computing {symbol}/{tf} ({candle_count} candles)...")
            try:
                df = pd.read_sql_query(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candlesticks WHERE symbol=? AND timeframe=? ORDER BY timestamp",
                    conn, params=(symbol, tf),
                )
                df = calculate_all_indicators(df)

                # Write all indicator rows
                written = 0
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
                    written += 1
                conn.commit()
                logger.info(f"  ✅ {symbol}/{tf}: {written} indicator rows written")
            except Exception as e:
                logger.error(f"  Failed {symbol}/{tf}: {e}")

    conn.close()
    logger.info("Indicator computation complete.")


if __name__ == '__main__':
    compute_all_indicators()
