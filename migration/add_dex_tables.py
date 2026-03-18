#!/usr/bin/env python3
"""
Migration: Add DEX integration tables and extend securities schema.

Creates:
  - dex_tokens table (DEX-specific metadata per security)
  - dex_transactions table (on-chain tx tracking)
  - Extends securities with chain, contract_address, is_dex columns
  - Seeds initial DEX securities (Base chain tokens)

Run from edoras directory:
    python3 migration/add_dex_tables.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "crypto_data.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    # Note: securities.exchange_id references exchanges_old (legacy FK from
    # before the trader migration recreated exchanges). Bankr only exists in
    # the new exchanges table, so we disable FK enforcement for this migration.
    # The join in config.get_security_info() uses the new exchanges table.
    conn.execute("PRAGMA foreign_keys = OFF")
    c = conn.cursor()

    print("=== DEX Tables Migration ===\n")

    # ── 1. Extend securities table ─────────────────────────────────────────
    print("[1/5] Extending securities table...")
    c.execute("PRAGMA table_info(securities)")
    cols = [r[1] for r in c.fetchall()]

    for col, typedef in [
        ("chain", "TEXT"),
        ("contract_address", "TEXT"),
        ("is_dex", "INTEGER DEFAULT 0"),
    ]:
        if col not in cols:
            c.execute(f"ALTER TABLE securities ADD COLUMN {col} {typedef}")
            print(f"  Added column: {col}")
        else:
            print(f"  Column {col} already exists, skipping")

    # ── 2. Create dex_tokens table ─────────────────────────────────────────
    print("[2/5] Creating dex_tokens table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS dex_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_id INTEGER NOT NULL,
            chain TEXT NOT NULL,
            contract_address TEXT NOT NULL,
            dex_platform TEXT,
            pair_address TEXT,
            liquidity REAL,
            volume_24h REAL,
            holder_count INTEGER,
            market_cap REAL,
            is_verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP,
            FOREIGN KEY (security_id) REFERENCES securities(id),
            UNIQUE(chain, contract_address)
        )
    """)
    print("  Created dex_tokens table")

    # ── 3. Create dex_transactions table ───────────────────────────────────
    print("[3/5] Creating dex_transactions table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS dex_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            portfolio_id INTEGER NOT NULL,
            security_id INTEGER,
            tx_hash TEXT UNIQUE,
            chain TEXT NOT NULL,
            action TEXT NOT NULL,
            from_token TEXT,
            to_token TEXT,
            amount_in REAL,
            amount_out REAL,
            price REAL,
            slippage_expected REAL,
            slippage_actual REAL,
            gas_used REAL,
            gas_price_gwei REAL,
            bankr_job_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (trade_id) REFERENCES trades(id),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
            FOREIGN KEY (security_id) REFERENCES securities(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_dex_tx_portfolio ON dex_transactions(portfolio_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_dex_tx_status ON dex_transactions(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_dex_tx_chain ON dex_transactions(chain)")
    print("  Created dex_transactions table + indexes")

    # ── 4. Seed DEX securities ─────────────────────────────────────────────
    print("[4/5] Seeding DEX securities...")

    # Get bankr exchange id
    c.execute("SELECT id FROM exchanges WHERE code = 'bankr'")
    row = c.fetchone()
    if not row:
        print("  ERROR: bankr exchange not found. Run add_traders.py first.")
        conn.close()
        sys.exit(1)
    bankr_id = row[0]

    dex_securities = [
        {
            "symbol": "WETH-BASE",
            "name": "Wrapped Ethereum (Base)",
            "security_type": "crypto",
            "asset_class": "crypto",
            "sector": "layer1",
            "chain": "base",
            "contract_address": "0x4200000000000000000000000000000000000006",
            "dex_platform": "native",
        },
        {
            "symbol": "USDC-BASE",
            "name": "USD Coin (Base)",
            "security_type": "stablecoin",
            "asset_class": "crypto",
            "sector": "stablecoin",
            "chain": "base",
            "contract_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "dex_platform": "native",
        },
        {
            "symbol": "VVV-BASE",
            "name": "Venice Token",
            "security_type": "crypto",
            "asset_class": "crypto",
            "sector": "ai",
            "chain": "base",
            "contract_address": "0x",  # placeholder — fill with real contract
            "dex_platform": "uniswap_v3",
        },
        {
            "symbol": "BNKR-BASE",
            "name": "Bankr Token",
            "security_type": "crypto",
            "asset_class": "crypto",
            "sector": "defi",
            "chain": "base",
            "contract_address": "0x",  # placeholder — fill with real contract
            "dex_platform": "uniswap_v3",
        },
    ]

    for sec in dex_securities:
        c.execute("SELECT id FROM securities WHERE symbol = ? AND exchange_id = ?",
                  (sec["symbol"], bankr_id))
        if c.fetchone():
            print(f"  {sec['symbol']} already exists, skipping")
            continue

        c.execute("""
            INSERT INTO securities
                (symbol, name, security_type, exchange_id, asset_class, sector,
                 quote_currency, is_tradeable, is_active, indicator_profile,
                 chain, contract_address, is_dex)
            VALUES (?, ?, ?, ?, ?, ?, 'USD', 1, 1, 'standard', ?, ?, 1)
        """, (
            sec["symbol"], sec["name"], sec["security_type"], bankr_id,
            sec["asset_class"], sec["sector"],
            sec["chain"], sec["contract_address"],
        ))
        sec_id = c.lastrowid

        # Also insert into dex_tokens for metadata tracking
        c.execute("""
            INSERT OR IGNORE INTO dex_tokens
                (security_id, chain, contract_address, dex_platform)
            VALUES (?, ?, ?, ?)
        """, (sec_id, sec["chain"], sec["contract_address"], sec["dex_platform"]))

        print(f"  Added: {sec['symbol']} ({sec['name']}) on {sec['chain']}")

    # ── 5. Summary ─────────────────────────────────────────────────────────
    print("\n[5/5] Verifying...")
    c.execute("SELECT COUNT(*) FROM securities WHERE is_dex = 1")
    dex_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM dex_tokens")
    token_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM dex_transactions")
    tx_count = c.fetchone()[0]
    print(f"  DEX securities: {dex_count}")
    print(f"  DEX token metadata records: {token_count}")
    print(f"  DEX transactions: {tx_count}")

    conn.commit()
    conn.close()
    print("\n=== Migration complete ===")


if __name__ == "__main__":
    migrate()
