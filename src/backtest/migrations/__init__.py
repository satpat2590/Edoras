"""
Schema migration framework for the Edoras backtesting module.

Tracks applied migrations in a _migrations table and runs them
idempotently. Designed for SQLite (additive-only: ALTER TABLE ADD COLUMN,
CREATE TABLE/VIEW/INDEX).
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the migrations tracking table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _get_applied(conn: sqlite3.Connection) -> set:
    """Return set of already-applied migration names."""
    cur = conn.execute("SELECT name FROM _migrations")
    return {row[0] for row in cur.fetchall()}


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _view_exists(conn: sqlite3.Connection, view_name: str) -> bool:
    """Check if a view exists."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
        (view_name,),
    )
    return cur.fetchone() is not None


def run_migration_001(conn: sqlite3.Connection) -> None:
    """Unify strategy_catalogue, strategy_performance, and strategy_registry.

    Adds cross-reference columns and a unified query view.
    """
    # Link strategy_performance to catalogue
    if not _column_exists(conn, "strategy_performance", "catalogue_id"):
        conn.execute(
            "ALTER TABLE strategy_performance ADD COLUMN catalogue_id INTEGER "
            "REFERENCES strategy_catalogue(id)"
        )

    # Link strategy_registry to qualifying backtest
    if not _column_exists(conn, "strategy_registry", "qualifying_catalogue_id"):
        conn.execute(
            "ALTER TABLE strategy_registry ADD COLUMN qualifying_catalogue_id INTEGER "
            "REFERENCES strategy_catalogue(id)"
        )
    if not _column_exists(conn, "strategy_registry", "qualified_at"):
        conn.execute(
            "ALTER TABLE strategy_registry ADD COLUMN qualified_at TEXT"
        )

    # Fix trade_outcomes: add proper FK columns
    if not _column_exists(conn, "trade_outcomes", "buy_trade_id"):
        conn.execute(
            "ALTER TABLE trade_outcomes ADD COLUMN buy_trade_id INTEGER "
            "REFERENCES trades(id)"
        )
    if not _column_exists(conn, "trade_outcomes", "sell_trade_id"):
        conn.execute(
            "ALTER TABLE trade_outcomes ADD COLUMN sell_trade_id INTEGER "
            "REFERENCES trades(id)"
        )

    # Source tracking on catalogue
    if not _column_exists(conn, "strategy_catalogue", "source"):
        conn.execute(
            "ALTER TABLE strategy_catalogue ADD COLUMN source TEXT DEFAULT 'backtest'"
        )

    # Unified query view
    if _view_exists(conn, "strategy_overview"):
        conn.execute("DROP VIEW strategy_overview")

    conn.execute("""
        CREATE VIEW strategy_overview AS
        SELECT
            r.id AS registry_id,
            r.name,
            r.strategy_type,
            r.is_active,
            c.id AS catalogue_id,
            c.symbol,
            c.timeframe,
            c.sharpe_ratio,
            c.total_return,
            c.max_drawdown,
            c.total_trades,
            c.catalogued_at,
            p.id AS performance_id,
            p.source AS perf_source,
            p.period_start,
            p.period_end
        FROM strategy_registry r
        LEFT JOIN strategy_catalogue c ON c.id = r.qualifying_catalogue_id
        LEFT JOIN strategy_performance p ON p.catalogue_id = c.id
    """)

    conn.commit()


def run_migrations(db_path: str) -> int:
    """Run all pending migrations. Returns count of newly applied migrations."""
    conn = sqlite3.connect(db_path)
    try:
        _ensure_migrations_table(conn)
        applied = _get_applied(conn)
        count = 0

        # Migration 001: Unify schema
        if "001_unify_schema" not in applied:
            logger.info("Applying migration 001_unify_schema")
            run_migration_001(conn)
            conn.execute(
                "INSERT INTO _migrations (name) VALUES (?)",
                ("001_unify_schema",),
            )
            conn.commit()
            count += 1

        return count
    finally:
        conn.close()
