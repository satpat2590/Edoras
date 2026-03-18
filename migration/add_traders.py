#!/usr/bin/env python3
"""
Migration: Add traders, trader_wallets, trader_portfolio_access tables.
Add trader_id FK to trades table.
Seed initial traders and relationships.

Run from edoras directory:
    python3 migration/add_traders.py
"""

import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "crypto_data.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    print("=== Trader Tables Migration ===\n")

    # ── 1. Expand exchange_type to support DEX and brokerages ────────────

    # SQLite doesn't support ALTER CHECK constraints, so we recreate via a
    # workaround: the CHECK is only enforced on INSERT/UPDATE; we'll add
    # new exchange types that fit the existing column. The CHECK constraint
    # on the original table allows: crypto, prediction, equity, index_provider.
    # We need to add 'dex' and 'brokerage'. Since SQLite can't ALTER CHECK,
    # we'll insert with the existing 'crypto' type for DEX (Bankr is crypto),
    # and note this for the data warehouse migration where we'll have a proper
    # enum. For now, we add a 'dex' exchange using a pragmatic approach.

    # Check if exchange_type CHECK allows 'dex' — it won't, so we need to
    # recreate the table. Let's do it properly.
    print("[1/6] Expanding exchange_type constraint...")
    c.execute("SELECT sql FROM sqlite_master WHERE name='exchanges'")
    current_ddl = c.fetchone()[0]

    if "'dex'" not in current_ddl:
        c.execute("PRAGMA foreign_keys = OFF")
        c.executescript("""
            ALTER TABLE exchanges RENAME TO exchanges_old;

            CREATE TABLE exchanges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                exchange_type TEXT NOT NULL
                    CHECK(exchange_type IN ('crypto', 'dex', 'prediction', 'equity',
                          'brokerage', 'index_provider')),
                api_module TEXT,
                base_url TEXT,
                supports_websocket INTEGER DEFAULT 0,
                supports_paper INTEGER DEFAULT 1,
                supports_live INTEGER DEFAULT 0,
                quote_currency TEXT DEFAULT 'USD',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO exchanges SELECT * FROM exchanges_old;
            DROP TABLE exchanges_old;
        """)
        c.execute("PRAGMA foreign_keys = ON")
        print("  Expanded: added 'dex', 'brokerage' types")
    else:
        print("  Already expanded, skipping")

    # ── 2. Create traders table ──────────────────────────────────────────

    print("[2/6] Creating traders table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS traders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            trader_type TEXT NOT NULL
                CHECK(trader_type IN ('agent', 'human', 'system')),
            description TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("  Created traders table")

    # ── 3. Create trader_wallets table ───────────────────────────────────

    print("[3/6] Creating trader_wallets table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS trader_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trader_id INTEGER NOT NULL,
            exchange_id INTEGER NOT NULL,
            wallet_type TEXT NOT NULL
                CHECK(wallet_type IN ('cex_api', 'dex_wallet', 'paper', 'brokerage')),
            wallet_ref TEXT,
            asset_classes TEXT DEFAULT '["crypto"]',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trader_id) REFERENCES traders(id),
            FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
            UNIQUE(trader_id, exchange_id, wallet_type)
        )
    """)
    print("  Created trader_wallets table")

    # ── 4. Create trader_portfolio_access table ──────────────────────────

    print("[4/6] Creating trader_portfolio_access table...")
    c.execute("""
        CREATE TABLE IF NOT EXISTS trader_portfolio_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trader_id INTEGER NOT NULL,
            portfolio_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'executor'
                CHECK(role IN ('executor', 'advisor', 'readonly')),
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trader_id) REFERENCES traders(id),
            FOREIGN KEY (portfolio_id) REFERENCES portfolios(id),
            UNIQUE(trader_id, portfolio_id)
        )
    """)
    print("  Created trader_portfolio_access table")

    # ── 5. Add trader_id to trades table ─────────────────────────────────

    print("[5/6] Adding trader_id to trades table...")
    c.execute("PRAGMA table_info(trades)")
    cols = [r[1] for r in c.fetchall()]
    if "trader_id" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN trader_id INTEGER REFERENCES traders(id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader_id)")
        print("  Added trader_id column + index")
    else:
        print("  trader_id already exists, skipping")

    # ── 6. Seed data ────────────────────────────────────────────────────

    print("[6/6] Seeding traders, wallets, and access...")

    # Add Bankr DEX exchange
    c.execute("SELECT id FROM exchanges WHERE code = 'bankr'")
    if not c.fetchone():
        c.execute("""
            INSERT INTO exchanges (code, name, exchange_type, base_url,
                                   supports_websocket, supports_paper, supports_live, notes)
            VALUES ('bankr', 'Bankr (DEX)', 'dex', 'https://api.bankr.bot',
                    0, 0, 1, 'AI-powered DEX trading via natural language. EVM + Solana wallets.')
        """)
        print("  Added exchange: Bankr (DEX)")

    # Traders
    traders = [
        ("aleph", "Aleph", "agent", "Main agent — general purpose, DEX trading via Bankr"),
        ("regi", "Regi", "agent", "Quant agent — signal-driven, CEX trading via Coinbase"),
        ("signal_engine", "Signal Engine", "system", "Automated signal generation and execution"),
        ("risk_engine", "Risk Engine", "system", "Automated risk management exits"),
        ("satyam", "Satyam", "human", "Portfolio owner — manual trades and oversight"),
    ]
    for code, name, ttype, desc in traders:
        c.execute("SELECT id FROM traders WHERE code = ?", (code,))
        if not c.fetchone():
            c.execute("INSERT INTO traders (code, name, trader_type, description) VALUES (?,?,?,?)",
                      (code, name, ttype, desc))
            print(f"  Added trader: {name} ({ttype})")

    # Get IDs
    def get_id(table, code):
        c.execute(f"SELECT id FROM {table} WHERE code = ?", (code,))
        return c.fetchone()[0]

    aleph_id = get_id("traders", "aleph")
    regi_id = get_id("traders", "regi")
    signal_id = get_id("traders", "signal_engine")
    risk_id = get_id("traders", "risk_engine")
    satyam_id = get_id("traders", "satyam")
    coinbase_id = get_id("exchanges", "coinbase")
    bankr_id = get_id("exchanges", "bankr")

    # Wallets
    wallets = [
        (regi_id, coinbase_id, "paper", "coinbase_paper", '["crypto"]'),
        (regi_id, coinbase_id, "cex_api", "coinbase_api", '["crypto"]'),
        (aleph_id, bankr_id, "dex_wallet", os.getenv("BANKR_API_KEY", "bankr:placeholder"), '["crypto"]'),
        (signal_id, coinbase_id, "paper", "coinbase_paper", '["crypto"]'),
        (risk_id, coinbase_id, "paper", "coinbase_paper", '["crypto"]'),
        (satyam_id, coinbase_id, "cex_api", "coinbase_api", '["crypto"]'),
    ]
    for trader_id, exchange_id, wtype, wref, aclass in wallets:
        c.execute("""SELECT id FROM trader_wallets
                     WHERE trader_id = ? AND exchange_id = ? AND wallet_type = ?""",
                  (trader_id, exchange_id, wtype))
        if not c.fetchone():
            c.execute("""INSERT INTO trader_wallets
                         (trader_id, exchange_id, wallet_type, wallet_ref, asset_classes)
                         VALUES (?,?,?,?,?)""",
                      (trader_id, exchange_id, wtype, wref, aclass))

    # Portfolio access
    access = [
        # Galadriel (paper) — Regi executes, Signal Engine executes, Risk Engine executes, Aleph advises
        (regi_id, 1, "executor"),
        (signal_id, 1, "executor"),
        (risk_id, 1, "executor"),
        (aleph_id, 1, "advisor"),
        (satyam_id, 1, "readonly"),
        # Thranduil (live, inactive) — Regi will execute
        (regi_id, 2, "executor"),
        (aleph_id, 2, "advisor"),
        (satyam_id, 2, "readonly"),
        # Elrond (tracked) — Satyam owns, Aleph can execute when activated
        (satyam_id, 3, "executor"),
        (aleph_id, 3, "executor"),
        (regi_id, 3, "readonly"),
    ]
    for trader_id, portfolio_id, role in access:
        c.execute("""SELECT id FROM trader_portfolio_access
                     WHERE trader_id = ? AND portfolio_id = ?""",
                  (trader_id, portfolio_id))
        if not c.fetchone():
            c.execute("""INSERT INTO trader_portfolio_access
                         (trader_id, portfolio_id, role) VALUES (?,?,?)""",
                      (trader_id, portfolio_id, role))

    # Backfill existing trades with trader_id based on decision_context
    print("\n  Backfilling trader_id on existing trades...")
    import json
    c.execute("SELECT id, decision_context, risk_event_type FROM trades WHERE trader_id IS NULL")
    trades = c.fetchall()
    for tid, ctx, risk_type in trades:
        trader = signal_id  # default: signal engine
        if risk_type in ("stop_loss", "trailing_stop", "take_profit", "circuit_breaker"):
            trader = risk_id
        elif ctx:
            try:
                d = json.loads(ctx)
                if d.get("signal_type") == "llm" or d.get("exit_reason") == "llm_signal":
                    trader = regi_id  # LLM trades go through Regi's trading_agent
                elif d.get("signal_type"):
                    trader = signal_id
                else:
                    trader = regi_id  # unknown context from trading_agent session
            except (json.JSONDecodeError, TypeError):
                pass
        else:
            trader = regi_id  # no context = early trading_agent trades
        c.execute("UPDATE trades SET trader_id = ? WHERE id = ?", (trader, tid))
    print(f"  Backfilled {len(trades)} trades")

    conn.commit()
    conn.close()
    print("\n=== Migration complete ===")


if __name__ == "__main__":
    migrate()
