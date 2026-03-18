#!/usr/bin/env python3
"""
Phase 1 — Additive, non-breaking warehouse redesign migration.

Creates new tables and adds nullable columns to existing tables.
All existing code continues to work unchanged.

Changes:
  1. Enhance `exchanges` → add venue fields (chain, chain_id, fee_model, settlement_type)
  2. Enhance `strategy_registry` → add strategy_type, parameters columns
  3. Create `portfolio_strategies` junction table (M:M portfolio ↔ strategy)
  4. Create `accounts` bridge table (portfolio ↔ venue)
  5. Create `transfers` table
  6. Create `portfolio_valuations` table
  7. Add DEX columns to `trades` (tx_hash, block_number, gas_used, gas_price_gwei, slippage_bps)
  8. Add `strategy_id` and `account_id` to `trades` (nullable)
  9. Add `account_id` to `positions` (nullable)
  10. Add `canonical_instrument_id` and `decimals` to `securities`

Seed data:
  - Venue metadata (chain, fee_model, settlement) for existing exchanges
  - Strategy types for existing strategy_registry rows
  - Account bridge records for each portfolio's venue relationship
  - Portfolio-strategy links from Galadriel's strategy_routes_json
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import DB_PATH

NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def migrate(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    print("=" * 60)
    print("Phase 1 — Additive warehouse redesign migration")
    print("=" * 60)

    # ── 1. Enhance exchanges with venue fields ─────────────────────────────

    print("\n[1/10] Enhancing exchanges table with venue fields...")
    for col, typedef in [
        ("chain", "TEXT"),
        ("chain_id", "INTEGER"),
        ("fee_model", "TEXT"),
        ("settlement_type", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE exchanges ADD COLUMN {col} {typedef}")
            print(f"  Added exchanges.{col}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  exchanges.{col} already exists")
            else:
                raise

    # Seed venue metadata
    venue_metadata = {
        "coinbase": {
            "fee_model": "maker_taker",
            "settlement_type": "instant",
        },
        "yfinance": {
            "fee_model": "commission",
            "settlement_type": "t_plus_1",
        },
        "polymarket": {
            "fee_model": "spread",
            "settlement_type": "on_chain",
            "chain": "polygon",
            "chain_id": 137,
        },
        "kalshi": {
            "fee_model": "spread",
            "settlement_type": "instant",
        },
        "bankr": {
            "fee_model": "gas",
            "settlement_type": "on_chain",
            "chain": "base",
            "chain_id": 8453,
        },
    }
    for code, meta in venue_metadata.items():
        sets = ", ".join(f"{k} = ?" for k in meta.keys())
        vals = list(meta.values()) + [code]
        cur.execute(f"UPDATE exchanges SET {sets} WHERE code = ?", vals)
        if cur.rowcount:
            print(f"  Updated venue metadata for {code}")

    # ── 2. Enhance strategy_registry ───────────────────────────────────────

    print("\n[2/10] Enhancing strategy_registry with strategy_type and parameters...")
    for col, typedef in [
        ("strategy_type", "TEXT"),
        ("parameters", "TEXT"),
    ]:
        try:
            cur.execute(f"ALTER TABLE strategy_registry ADD COLUMN {col} {typedef}")
            print(f"  Added strategy_registry.{col}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  strategy_registry.{col} already exists")
            else:
                raise

    # Seed strategy types
    strategy_types = {
        "ScoreBased": "momentum",
        "ScoreBasedRelaxed": "momentum",
        "BollingerReversion": "mean_reversion",
        "MultiSignal": "multi_factor",
        "MACDCross": "momentum",
        "ADXTrend": "trend_following",
        "EnhancedScoreBased": "momentum",
    }
    for name, stype in strategy_types.items():
        cur.execute(
            "UPDATE strategy_registry SET strategy_type = ? WHERE name = ? AND strategy_type IS NULL",
            (stype, name),
        )

    # Copy default_params_json → parameters where parameters is NULL
    cur.execute(
        "UPDATE strategy_registry SET parameters = default_params_json "
        "WHERE parameters IS NULL AND default_params_json IS NOT NULL"
    )
    print("  Seeded strategy types and parameters")

    # ── 3. Create portfolio_strategies junction table ──────────────────────

    print("\n[3/10] Creating portfolio_strategies junction table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            strategy_id INTEGER NOT NULL REFERENCES strategy_registry(id),
            allocation_pct REAL,
            is_active INTEGER DEFAULT 1,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            retired_at TIMESTAMP,
            UNIQUE(portfolio_id, strategy_id)
        )
    """)

    # Seed from Galadriel's strategy_routes_json
    row = cur.execute(
        "SELECT id, strategy_routes_json FROM portfolios WHERE id = 1"
    ).fetchone()
    if row and row[1]:
        routes = json.loads(row[1])
        strategy_names = set()
        for sym_cfg in routes.values():
            sname = sym_cfg.get("strategy")
            if sname:
                strategy_names.add(sname)

        for sname in strategy_names:
            sr = cur.execute(
                "SELECT id FROM strategy_registry WHERE name = ?", (sname,)
            ).fetchone()
            if sr:
                cur.execute(
                    "INSERT OR IGNORE INTO portfolio_strategies "
                    "(portfolio_id, strategy_id, is_active, assigned_at) "
                    "VALUES (?, ?, 1, ?)",
                    (1, sr[0], NOW),
                )
        print(f"  Seeded {len(strategy_names)} portfolio-strategy links for Galadriel")

    # ── 4. Create accounts bridge table ────────────────────────────────────

    print("\n[4/10] Creating accounts bridge table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            venue_id INTEGER NOT NULL REFERENCES exchanges(id),
            account_name TEXT NOT NULL,
            account_external_id TEXT,
            account_type TEXT CHECK(account_type IN (
                'api_key', 'wallet', 'subaccount', 'brokerage', 'paper'
            )),
            status TEXT DEFAULT 'active' CHECK(status IN (
                'active', 'suspended', 'closed'
            )),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_portfolio_venue_ext
        ON accounts(portfolio_id, venue_id, account_external_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_accounts_portfolio
        ON accounts(portfolio_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_accounts_venue
        ON accounts(venue_id)
    """)

    # Seed accounts from existing portfolio-venue relationships
    # Each portfolio currently maps to one venue implicitly
    account_seeds = [
        # (portfolio_id, venue_code, account_name, external_id, account_type)
        (1, "coinbase", "Galadriel-Coinbase", "coinbase_paper", "paper"),
        (2, "coinbase", "Thranduil-Coinbase", "coinbase_live", "api_key"),
        (3, "coinbase", "Elrond-Coinbase", "coinbase_tracked", "api_key"),
        (4, "bankr", "Arwen-Bankr-Base", os.getenv("DEX_WALLET_ADDRESS", ""), "wallet"),
    ]
    for pid, vcode, aname, ext_id, atype in account_seeds:
        vid = cur.execute(
            "SELECT id FROM exchanges WHERE code = ?", (vcode,)
        ).fetchone()
        if vid:
            cur.execute(
                "INSERT OR IGNORE INTO accounts "
                "(portfolio_id, venue_id, account_name, account_external_id, "
                "account_type, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                (pid, vid[0], aname, ext_id, atype, NOW, NOW),
            )
    print(f"  Seeded {len(account_seeds)} account bridge records")

    # ── 5. Create transfers table ──────────────────────────────────────────

    print("\n[5/10] Creating transfers table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_account_id INTEGER REFERENCES accounts(id),
            to_account_id INTEGER REFERENCES accounts(id),
            instrument_id INTEGER REFERENCES securities(id),
            symbol TEXT,
            quantity REAL NOT NULL,
            transfer_type TEXT NOT NULL CHECK(transfer_type IN (
                'deposit', 'withdrawal', 'internal_transfer', 'bridge'
            )),
            transfer_timestamp TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN (
                'pending', 'confirmed', 'failed'
            )),
            tx_hash TEXT,
            fee_amount REAL DEFAULT 0.0,
            fee_currency TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_transfers_from
        ON transfers(from_account_id, transfer_timestamp)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_transfers_to
        ON transfers(to_account_id, transfer_timestamp)
    """)
    print("  Created transfers table")

    # ── 6. Create portfolio_valuations table ───────────────────────────────

    print("\n[6/10] Creating portfolio_valuations table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_valuations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
            valuation_timestamp TIMESTAMP NOT NULL,
            total_nav_usd REAL,
            total_cost_basis_usd REAL,
            total_unrealized_pnl_usd REAL,
            total_realized_pnl_usd REAL,
            total_fees_usd REAL,
            account_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_valuations_portfolio_time
        ON portfolio_valuations(portfolio_id, valuation_timestamp)
    """)

    # Backfill from paper_snapshots
    existing = cur.execute(
        "SELECT COUNT(*) FROM portfolio_valuations"
    ).fetchone()[0]
    if existing == 0:
        cur.execute("""
            INSERT INTO portfolio_valuations
                (portfolio_id, valuation_timestamp, total_nav_usd, account_count)
            SELECT
                portfolio_id,
                date || ' 17:00:00',
                portfolio_value,
                1
            FROM paper_snapshots
            ORDER BY date
        """)
        backfilled = cur.rowcount
        print(f"  Backfilled {backfilled} valuations from paper_snapshots")
    else:
        print(f"  portfolio_valuations already has {existing} rows, skipping backfill")

    # ── 7. Add DEX columns to trades ───────────────────────────────────────

    print("\n[7/10] Adding DEX columns to trades...")
    for col, typedef in [
        ("tx_hash", "TEXT"),
        ("block_number", "INTEGER"),
        ("gas_used", "INTEGER"),
        ("gas_price_gwei", "REAL"),
        ("slippage_bps", "REAL"),
    ]:
        try:
            cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {typedef}")
            print(f"  Added trades.{col}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  trades.{col} already exists")
            else:
                raise

    # ── 8. Add strategy_id and account_id to trades ────────────────────────

    print("\n[8/10] Adding strategy_id and account_id to trades...")
    for col, typedef in [
        ("strategy_id", "INTEGER REFERENCES strategy_registry(id)"),
        ("account_id", "INTEGER REFERENCES accounts(id)"),
    ]:
        colname = col.split()[0] if " " in col else col
        try:
            cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {typedef}")
            print(f"  Added trades.{colname}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  trades.{colname} already exists")
            else:
                raise

    # Backfill account_id on existing trades (all portfolio_id=1 → Galadriel-Coinbase account)
    gal_account = cur.execute(
        "SELECT id FROM accounts WHERE portfolio_id = 1 AND account_type = 'paper'"
    ).fetchone()
    if gal_account:
        cur.execute(
            "UPDATE trades SET account_id = ? WHERE portfolio_id = 1 AND account_id IS NULL",
            (gal_account[0],),
        )
        updated = cur.rowcount
        if updated:
            print(f"  Backfilled account_id on {updated} existing trades")

    # Backfill strategy_id from decision_context JSON where possible
    rows = cur.execute(
        "SELECT id, decision_context FROM trades WHERE strategy_id IS NULL AND decision_context IS NOT NULL"
    ).fetchall()
    strategy_cache = {}
    strat_updated = 0
    for tid, ctx_json in rows:
        try:
            ctx = json.loads(ctx_json)
            sname = ctx.get("strategy") or ctx.get("strategy_name")
            if not sname:
                continue
            if sname not in strategy_cache:
                sr = cur.execute(
                    "SELECT id FROM strategy_registry WHERE name = ?", (sname,)
                ).fetchone()
                strategy_cache[sname] = sr[0] if sr else None
            sid = strategy_cache.get(sname)
            if sid:
                cur.execute(
                    "UPDATE trades SET strategy_id = ? WHERE id = ?", (sid, tid)
                )
                strat_updated += 1
        except (json.JSONDecodeError, TypeError):
            continue
    if strat_updated:
        print(f"  Backfilled strategy_id on {strat_updated} trades from decision_context")

    # ── 9. Add account_id to positions ─────────────────────────────────────

    print("\n[9/10] Adding account_id to positions...")
    try:
        cur.execute("ALTER TABLE positions ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
        print("  Added positions.account_id")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  positions.account_id already exists")
        else:
            raise

    # Backfill: portfolio_id=1 open positions → Galadriel-Coinbase account
    if gal_account:
        cur.execute(
            "UPDATE positions SET account_id = ? WHERE portfolio_id = 1 AND account_id IS NULL",
            (gal_account[0],),
        )
        updated = cur.rowcount
        if updated:
            print(f"  Backfilled account_id on {updated} existing positions")

    # Backfill: portfolio_id=4 positions → Arwen-Bankr account
    arwen_account = cur.execute(
        "SELECT id FROM accounts WHERE portfolio_id = 4"
    ).fetchone()
    if arwen_account:
        cur.execute(
            "UPDATE positions SET account_id = ? WHERE portfolio_id = 4 AND account_id IS NULL",
            (arwen_account[0],),
        )
        updated = cur.rowcount
        if updated:
            print(f"  Backfilled account_id on {updated} Arwen positions")

    # ── 10. Add canonical_instrument_id and decimals to securities ─────────

    print("\n[10/10] Adding canonical_instrument_id and decimals to securities...")
    for col, typedef in [
        ("canonical_instrument_id", "INTEGER REFERENCES securities(id)"),
        ("decimals", "INTEGER"),
    ]:
        colname = col.split()[0] if " " in col else col
        try:
            cur.execute(f"ALTER TABLE securities ADD COLUMN {col} {typedef}")
            print(f"  Added securities.{colname}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  securities.{colname} already exists")
            else:
                raise

    # Set decimals for known DEX tokens
    dex_decimals = {
        "WETH-BASE": 18,
        "USDC-BASE": 6,
        "VVV-BASE": 18,
        "BNKR-BASE": 18,
    }
    for sym, dec in dex_decimals.items():
        cur.execute(
            "UPDATE securities SET decimals = ? WHERE symbol = ? AND decimals IS NULL",
            (dec, sym),
        )

    # Link DEX tokens to their CEX canonical counterparts
    # WETH-BASE → ETH-USD (same underlying asset)
    eth_id = cur.execute(
        "SELECT id FROM securities WHERE symbol = 'ETH-USD' AND exchange_id = 1 LIMIT 1"
    ).fetchone()
    if eth_id:
        cur.execute(
            "UPDATE securities SET canonical_instrument_id = ? "
            "WHERE symbol = 'WETH-BASE' AND canonical_instrument_id IS NULL",
            (eth_id[0],),
        )
    # USDC-BASE is its own canonical (stablecoin, no CEX equivalent tracked)
    print("  Set decimals and canonical links for DEX tokens")

    # ── Commit ─────────────────────────────────────────────────────────────

    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print("Phase 1 migration complete.")
    print("=" * 60)
    print("""
Summary of changes:
  Tables created:  accounts, portfolio_strategies, transfers, portfolio_valuations
  Tables modified: exchanges (+4 cols), strategy_registry (+2 cols),
                   trades (+7 cols), positions (+1 col), securities (+2 cols)
  Data seeded:     4 accounts, portfolio-strategy links, venue metadata,
                   strategy types, trade backfills

  All existing code continues to work unchanged.
  New columns are nullable — no existing queries break.
""")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase 1 warehouse redesign migration")
    parser.add_argument("--db", default=DB_PATH, help="Database path")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — would execute Phase 1 migration on:", args.db)
        print("  Create: accounts, portfolio_strategies, transfers, portfolio_valuations")
        print("  Alter: exchanges, strategy_registry, trades, positions, securities")
    else:
        migrate(args.db)
