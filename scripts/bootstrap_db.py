#!/usr/bin/env python3
"""Bootstrap empty databases with the full schema.

Run this once on a fresh machine after cloning the repo.
It creates crypto_data.db with all tables (enhanced + legacy operational)
and seeds the default portfolios.

Usage:
    python bootstrap_db.py            # creates crypto_data.db in this directory
    python bootstrap_db.py --db /path/to/crypto_data.db
"""

import argparse
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "crypto_data.db")

# ── Enhanced schema (from schema/enhanced_schema_sqlite.sql) ────────────────

ENHANCED_SCHEMA = os.path.join(BASE_DIR, "schema", "enhanced_schema_sqlite.sql")

# ── Operational tables created by individual scripts ────────────────────────
# These aren't in the enhanced schema file but are required at runtime.

OPERATIONAL_TABLES = """
-- Candlestick OHLCV data (crypto + equity)
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
);

-- Technical indicators (RSI, MACD, BB, etc.)
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
);

-- Portfolio analysis / signal summaries
CREATE TABLE IF NOT EXISTS portfolio_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    short_term_signal REAL,
    medium_term_signal REAL,
    long_term_signal REAL,
    trend_strength REAL,
    volatility_level REAL,
    support_1 REAL, support_2 REAL,
    resistance_1 REAL, resistance_2 REAL,
    action TEXT,
    confidence REAL,
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(analysis_date, symbol, timeframe)
);

-- Market regime (daily VIX + correlation snapshot)
CREATE TABLE IF NOT EXISTS market_regime (
    date TEXT PRIMARY KEY,
    vix_value REAL,
    regime TEXT,
    btc_sp500_corr REAL,
    btc_nasdaq_corr REAL
);

-- Paper portfolio snapshots (daily summary)
CREATE TABLE IF NOT EXISTS paper_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    cash REAL NOT NULL,
    num_positions INTEGER NOT NULL,
    positions_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date)
);

-- Backward-compat view for code that still references paper_trades
CREATE VIEW IF NOT EXISTS paper_trades AS
SELECT
    id, portfolio_id, symbol, side,
    quantity, price, amount_usd, fee,
    order_type, status,
    portfolio_value, cash_after,
    created_at AS timestamp, created_at
FROM trades
WHERE portfolio_id = 1;
"""

# ── Portfolio seed data ─────────────────────────────────────────────────────

SEED_PORTFOLIOS = """
INSERT OR REPLACE INTO portfolios (id, name, mode, asset_class, initial_capital, currency, symbols_json, is_active)
VALUES
    (1, 'Galadriel',  'paper',   'crypto', 1000.0, 'USD',
     '["ETH-USD","BTC-USD","XRP-USD","TROLL-USD","BONK-USD","FET-USD","AMP-USD","GRT-USD"]', 1),
    (2, 'Thranduil',  'live',    'crypto', 1000.0, 'USD', '[]', 0),
    (3, 'Elrond',     'tracked', 'crypto', 0.0,    'USD', '[]', 1);
"""


def bootstrap(db_path: str):
    if os.path.exists(db_path):
        print(f"Database already exists: {db_path}")
        print("Delete it first if you want a fresh bootstrap.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Run enhanced schema SQL file
    if os.path.exists(ENHANCED_SCHEMA):
        print(f"Applying enhanced schema from {ENHANCED_SCHEMA} ...")
        with open(ENHANCED_SCHEMA) as f:
            cur.executescript(f.read())
    else:
        print(f"WARNING: {ENHANCED_SCHEMA} not found, skipping enhanced tables.")

    # 2. Create operational tables
    print("Creating operational tables ...")
    cur.executescript(OPERATIONAL_TABLES)

    # 3. Add mode/asset_class columns to portfolios if not present
    # (enhanced schema might not have them)
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(portfolios)").fetchall()}
    for col, typedef in [
        ("mode", "TEXT DEFAULT 'paper'"),
        ("asset_class", "TEXT DEFAULT 'crypto'"),
        ("symbols_json", "TEXT"),
        ("strategy_routes_json", "TEXT"),
        ("default_timeframe", "TEXT DEFAULT '1d'"),
        ("state_file", "TEXT"),
    ]:
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE portfolios ADD COLUMN {col} {typedef}")

    # 4. Seed portfolios
    print("Seeding default portfolios ...")
    cur.executescript(SEED_PORTFOLIOS)

    conn.commit()

    # Summary
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    views = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()]
    conn.close()

    print(f"\nBootstrapped {db_path}")
    print(f"  Tables: {', '.join(tables)}")
    print(f"  Views:  {', '.join(views) if views else '(none)'}")
    print("\nNext steps:")
    print("  1. Run historical_backfill.py to populate candlestick data (~400 days)")
    print("  2. Run compute_all_indicators.py to generate technical indicators")
    print("  3. Run correlation_tracker.py to seed market regime data")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap trading system database")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to create database")
    args = parser.parse_args()
    bootstrap(args.db)
