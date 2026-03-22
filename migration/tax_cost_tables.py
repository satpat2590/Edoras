#!/usr/bin/env python3
"""
Tax & Cost Tracking — Migration script.

Creates:
  1. tax_lots table
  2. lot_dispositions table
  3. cost_ledger table
  4. v_realized_gains view
  5. v_open_lots view
  6. v_cost_summary view
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_PATH

import sqlite3


def migrate(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    print("=" * 60)
    print("Tax & Cost Tracking — Migration")
    print("=" * 60)

    # ── 1. tax_lots ──────────────────────────────────────────────────────────

    print("\n[1/6] Creating tax_lots table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tax_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            symbol TEXT NOT NULL,
            buy_trade_id INTEGER NOT NULL REFERENCES trades(id),
            acquired_at TIMESTAMP NOT NULL,
            original_quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            cost_basis_per_unit REAL NOT NULL,
            total_cost_basis REAL NOT NULL,
            status TEXT CHECK(status IN ('open', 'depleted')) DEFAULT 'open',
            strategy_id INTEGER REFERENCES strategy_registry(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tax_lots_portfolio_symbol
        ON tax_lots(portfolio_id, symbol) WHERE status = 'open'
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tax_lots_acquired
        ON tax_lots(portfolio_id, acquired_at)
    """)
    print("  Created tax_lots table")

    # ── 2. lot_dispositions ──────────────────────────────────────────────────

    print("\n[2/6] Creating lot_dispositions table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lot_dispositions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tax_lot_id INTEGER NOT NULL REFERENCES tax_lots(id),
            sell_trade_id INTEGER NOT NULL REFERENCES trades(id),
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            proceeds_per_unit REAL NOT NULL,
            cost_basis_per_unit REAL NOT NULL,
            realized_gain_usd REAL NOT NULL,
            holding_period_days INTEGER NOT NULL,
            term TEXT CHECK(term IN ('short', 'long')) NOT NULL,
            is_wash_sale INTEGER DEFAULT 0,
            wash_disallowed_usd REAL DEFAULT 0.0,
            replacement_lot_id INTEGER REFERENCES tax_lots(id),
            disposition_method TEXT DEFAULT 'fifo' CHECK(disposition_method IN ('fifo', 'specific_id')),
            disposed_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispositions_sell_trade
        ON lot_dispositions(sell_trade_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_dispositions_portfolio_time
        ON lot_dispositions(portfolio_id, disposed_at)
    """)
    print("  Created lot_dispositions table")

    # ── 3. cost_ledger ───────────────────────────────────────────────────────

    print("\n[3/6] Creating cost_ledger table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cost_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id),
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            symbol TEXT NOT NULL,
            cost_type TEXT NOT NULL CHECK(cost_type IN ('exchange_fee', 'gas_fee', 'slippage', 'network_fee')),
            amount_usd REAL NOT NULL,
            strategy_id INTEGER REFERENCES strategy_registry(id),
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_ledger_trade
        ON cost_ledger(trade_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_ledger_portfolio_type
        ON cost_ledger(portfolio_id, cost_type)
    """)
    print("  Created cost_ledger table")

    # ── 4. v_realized_gains view ─────────────────────────────────────────────

    print("\n[4/6] Creating v_realized_gains view...")
    cur.execute("DROP VIEW IF EXISTS v_realized_gains")
    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_realized_gains AS
        SELECT d.portfolio_id, d.symbol, d.term, d.disposed_at, d.quantity,
            d.cost_basis_per_unit, d.proceeds_per_unit, d.realized_gain_usd,
            d.holding_period_days, d.is_wash_sale, d.wash_disallowed_usd,
            d.realized_gain_usd - d.wash_disallowed_usd AS adjusted_gain_usd,
            tl.strategy_id, sr.name AS strategy_name
        FROM lot_dispositions d
        JOIN tax_lots tl ON d.tax_lot_id = tl.id
        LEFT JOIN strategy_registry sr ON tl.strategy_id = sr.id
    """)
    print("  Created v_realized_gains view")

    # ── 5. v_open_lots view ──────────────────────────────────────────────────

    print("\n[5/6] Creating v_open_lots view...")
    cur.execute("DROP VIEW IF EXISTS v_open_lots")
    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_open_lots AS
        SELECT tl.portfolio_id, tl.symbol, tl.acquired_at, tl.remaining_quantity,
            tl.cost_basis_per_unit,
            tl.remaining_quantity * tl.cost_basis_per_unit AS remaining_cost_basis,
            CAST(julianday('now') - julianday(tl.acquired_at) AS INTEGER) AS days_held,
            CASE WHEN julianday('now') - julianday(tl.acquired_at) >= 365 THEN 'long' ELSE 'short' END AS projected_term,
            sr.name AS strategy_name
        FROM tax_lots tl
        LEFT JOIN strategy_registry sr ON tl.strategy_id = sr.id
        WHERE tl.status = 'open' AND tl.remaining_quantity > 0
    """)
    print("  Created v_open_lots view")

    # ── 6. v_cost_summary view ───────────────────────────────────────────────

    print("\n[6/6] Creating v_cost_summary view...")
    cur.execute("DROP VIEW IF EXISTS v_cost_summary")
    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_cost_summary AS
        SELECT cl.portfolio_id, cl.cost_type, cl.strategy_id, sr.name AS strategy_name,
            cl.symbol, SUM(cl.amount_usd) AS total_cost_usd, COUNT(*) AS event_count,
            AVG(cl.amount_usd) AS avg_cost_usd
        FROM cost_ledger cl
        LEFT JOIN strategy_registry sr ON cl.strategy_id = sr.id
        GROUP BY cl.portfolio_id, cl.cost_type, cl.strategy_id, cl.symbol
    """)
    print("  Created v_cost_summary view")

    # ── Commit ───────────────────────────────────────────────────────────────

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("Tax & Cost Tracking migration complete.")
    print("=" * 60)
    print("""
Summary:
  Tables created:  tax_lots, lot_dispositions, cost_ledger
  Views created:   v_realized_gains, v_open_lots, v_cost_summary
  Indexes created: 6 indexes across all tables

  Run tax_processor.py to backfill from existing trades.
""")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tax & Cost Tracking migration")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — would execute Tax & Cost migration on:", args.db)
        print("  Create: tax_lots, lot_dispositions, cost_ledger")
        print("  Views:  v_realized_gains, v_open_lots, v_cost_summary")
    else:
        migrate(args.db)
