"""
Backfill migration: link existing strategy_performance rows to
strategy_catalogue entries, and set qualifying_catalogue_id on
strategy_registry rows.

Safe to run multiple times — only updates NULL columns.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


def backfill_performance_catalogue_links(conn: sqlite3.Connection) -> int:
    """Link strategy_performance rows to their best-matching catalogue entries.

    Match criteria: same strategy_name, symbol, timeframe, and overlapping date range.
    Only updates rows where catalogue_id IS NULL.

    Returns count of rows updated.
    """
    cur = conn.execute("""
        UPDATE strategy_performance
        SET catalogue_id = (
            SELECT c.id
            FROM strategy_catalogue c
            WHERE c.strategy_name = strategy_performance.strategy_name
              AND c.symbol = strategy_performance.symbol
              AND c.timeframe = strategy_performance.timeframe
              AND c.start_date <= strategy_performance.period_start
              AND c.end_date >= strategy_performance.period_end
            ORDER BY c.sharpe_ratio DESC
            LIMIT 1
        )
        WHERE catalogue_id IS NULL
    """)
    conn.commit()
    updated = cur.rowcount
    logger.info(f"Backfill: linked {updated} strategy_performance rows to catalogue")
    return updated


def backfill_registry_qualifying(conn: sqlite3.Connection) -> int:
    """Set qualifying_catalogue_id on strategy_registry rows.

    For each active strategy, find its best catalogue entry (highest Sharpe
    with >= 3 trades) and link it.

    Only updates rows where qualifying_catalogue_id IS NULL.

    Returns count of rows updated.
    """
    cur = conn.execute("""
        UPDATE strategy_registry
        SET qualifying_catalogue_id = (
            SELECT c.id
            FROM strategy_catalogue c
            WHERE c.strategy_name = strategy_registry.name
              AND c.total_trades >= 3
            ORDER BY c.sharpe_ratio DESC
            LIMIT 1
        ),
        qualified_at = datetime('now')
        WHERE qualifying_catalogue_id IS NULL
          AND is_active = 1
    """)
    conn.commit()
    updated = cur.rowcount
    logger.info(f"Backfill: linked {updated} strategy_registry rows to qualifying catalogue entry")
    return updated


def backfill_trade_outcome_links(conn: sqlite3.Connection) -> int:
    """Best-effort link trade_outcomes to trades via symbol + date matching.

    Matches buy_trade_id to the most recent BUY trade for the same symbol
    on or before entry_date, and sell_trade_id to the first SELL trade
    on or after exit_date.

    Only updates rows where buy_trade_id IS NULL.

    Returns count of rows updated.
    """
    cur = conn.execute("""
        UPDATE trade_outcomes
        SET buy_trade_id = (
            SELECT t.id
            FROM trades t
            WHERE t.symbol = trade_outcomes.symbol
              AND t.side = 'BUY'
              AND date(t.created_at) <= date(trade_outcomes.entry_date)
              AND t.portfolio_id = COALESCE(trade_outcomes.portfolio_id, 1)
            ORDER BY t.created_at DESC
            LIMIT 1
        )
        WHERE buy_trade_id IS NULL
    """)
    buy_count = cur.rowcount

    cur = conn.execute("""
        UPDATE trade_outcomes
        SET sell_trade_id = (
            SELECT t.id
            FROM trades t
            WHERE t.symbol = trade_outcomes.symbol
              AND t.side = 'SELL'
              AND date(t.created_at) >= date(trade_outcomes.exit_date)
              AND t.portfolio_id = COALESCE(trade_outcomes.portfolio_id, 1)
            ORDER BY t.created_at ASC
            LIMIT 1
        )
        WHERE sell_trade_id IS NULL
    """)
    sell_count = cur.rowcount
    conn.commit()

    total = buy_count + sell_count
    logger.info(f"Backfill: linked {buy_count} buy + {sell_count} sell trade_outcome FK rows")
    return total


def run_backfill(db_path: str) -> dict:
    """Run all backfill operations. Returns summary dict."""
    conn = sqlite3.connect(db_path)
    try:
        perf = backfill_performance_catalogue_links(conn)
        reg = backfill_registry_qualifying(conn)
        outcomes = backfill_trade_outcome_links(conn)
        return {
            "performance_links": perf,
            "registry_links": reg,
            "outcome_links": outcomes,
        }
    finally:
        conn.close()
