#!/usr/bin/env python3
"""
Strategy Performance Tracker — stores strategy metrics and signal logs in the database.

Two tables:
  strategy_performance  — periodic snapshots (backtest results, rolling paper stats)
  strategy_signals_log  — every signal generated, with execution and outcome tracking

Usage:
  # Store backtest results
  tracker = StrategyTracker()
  tracker.store_backtest_result(strategy, symbol, timeframe, metrics_dict)

  # Log a live signal
  tracker.log_signal(strategy, symbol, timeframe, action, strength, reason, executed)

  # Update signal outcome when position closes
  tracker.update_signal_outcome(signal_id, outcome_pct, exit_reason)

  # Query analytics
  tracker.get_strategy_summary()
  tracker.get_signal_hit_rate(strategy, symbol)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from config import ACTIVE_PORTFOLIO_ID
except ImportError:
    ACTIVE_PORTFOLIO_ID = 1

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_data.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('backtest', 'paper', 'live')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    total_return REAL,
    annualized_return REAL,
    sharpe_ratio REAL,
    sortino_ratio REAL,
    max_drawdown REAL,
    win_rate REAL,
    profit_factor REAL,
    total_trades INTEGER,
    avg_win REAL,
    avg_loss REAL,
    avg_holding_days REAL,
    calmar_ratio REAL,
    parameters_json TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sp_strategy ON strategy_performance(strategy_name);
CREATE INDEX IF NOT EXISTS idx_sp_symbol ON strategy_performance(symbol);
CREATE INDEX IF NOT EXISTS idx_sp_source ON strategy_performance(source);

CREATE TABLE IF NOT EXISTS strategy_signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_time TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    strength REAL,
    reason TEXT,
    was_executed INTEGER DEFAULT 0,
    entry_price REAL,
    exit_price REAL,
    outcome_pct REAL,
    exit_reason TEXT,
    market_regime TEXT,
    adx REAL,
    rsi REAL,
    skip_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ssl_strategy ON strategy_signals_log(strategy_name);
CREATE INDEX IF NOT EXISTS idx_ssl_symbol ON strategy_signals_log(symbol);
CREATE INDEX IF NOT EXISTS idx_ssl_time ON strategy_signals_log(signal_time);
"""


class StrategyTracker:
    """Tracks strategy performance metrics and individual signals."""

    def __init__(self, db_path: str = DB_PATH, portfolio_id: int = ACTIVE_PORTFOLIO_ID):
        self.db_path = db_path
        self.portfolio_id = portfolio_id
        self._ensure_tables()

    def _ensure_tables(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_SQL)
        # Migrate: add skip_reason column if missing (added 2026-03-29)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(strategy_signals_log)")]
        if "skip_reason" not in cols:
            conn.execute("ALTER TABLE strategy_signals_log ADD COLUMN skip_reason TEXT")
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Store performance snapshots ───────────────────────────────────────

    def store_backtest_result(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        period_start: str,
        period_end: str,
        metrics: dict,
        parameters: dict = None,
        notes: str = None,
    ):
        """Store a backtest result as a strategy_performance row."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO strategy_performance "
            "(strategy_name, symbol, timeframe, source, period_start, period_end, "
            "total_return, annualized_return, sharpe_ratio, sortino_ratio, max_drawdown, "
            "win_rate, profit_factor, total_trades, avg_win, avg_loss, avg_holding_days, "
            "calmar_ratio, parameters_json, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                strategy_name, symbol, timeframe, "backtest", period_start, period_end,
                metrics.get("total_return"),
                metrics.get("annualized_return"),
                metrics.get("sharpe_ratio"),
                metrics.get("sortino_ratio"),
                metrics.get("max_drawdown"),
                metrics.get("win_rate"),
                metrics.get("profit_factor"),
                metrics.get("total_trades"),
                metrics.get("avg_win"),
                metrics.get("avg_loss"),
                metrics.get("avg_holding_days"),
                metrics.get("calmar_ratio"),
                json.dumps(parameters) if parameters else None,
                notes,
            ),
        )
        conn.commit()
        conn.close()

    def store_rolling_performance(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        source: str,
        period_start: str,
        period_end: str,
        metrics: dict,
        notes: str = None,
    ):
        """Store a rolling paper/live performance snapshot."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO strategy_performance "
            "(strategy_name, symbol, timeframe, source, period_start, period_end, "
            "total_return, sharpe_ratio, sortino_ratio, max_drawdown, "
            "win_rate, profit_factor, total_trades, avg_win, avg_loss, "
            "avg_holding_days, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                strategy_name, symbol, timeframe, source, period_start, period_end,
                metrics.get("total_return"),
                metrics.get("sharpe_ratio"),
                metrics.get("sortino_ratio"),
                metrics.get("max_drawdown"),
                metrics.get("win_rate"),
                metrics.get("profit_factor"),
                metrics.get("total_trades"),
                metrics.get("avg_win"),
                metrics.get("avg_loss"),
                metrics.get("avg_holding_days"),
                notes,
            ),
        )
        conn.commit()
        conn.close()

    # ── Signal logging ────────────────────────────────────────────────────

    def log_signal(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        action: str,
        strength: float,
        reason: str,
        was_executed: bool = False,
        entry_price: float = None,
        market_regime: str = None,
        adx: float = None,
        rsi: float = None,
    ) -> int:
        """Log a signal and return the signal ID for later outcome update.

        Deduplicates: if an identical signal (same strategy/symbol/action) was
        logged within the last 30 minutes, returns the existing ID instead.
        """
        conn = self._connect()
        # Check for recent duplicate within the same portfolio
        cutoff = (datetime.now() - timedelta(minutes=30)).isoformat()
        existing = conn.execute(
            "SELECT id FROM strategy_signals_log "
            "WHERE strategy_name=? AND symbol=? AND action=? AND signal_time > ? "
            "AND portfolio_id=? "
            "ORDER BY id DESC LIMIT 1",
            (strategy_name, symbol, action, cutoff, self.portfolio_id),
        ).fetchone()
        if existing:
            conn.close()
            return existing["id"]

        cursor = conn.execute(
            "INSERT INTO strategy_signals_log "
            "(strategy_name, symbol, timeframe, signal_time, action, strength, reason, "
            "was_executed, entry_price, market_regime, adx, rsi, portfolio_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                strategy_name, symbol, timeframe,
                datetime.now().isoformat(),
                action, strength, reason,
                1 if was_executed else 0,
                entry_price, market_regime, adx, rsi,
                self.portfolio_id,
            ),
        )
        signal_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return signal_id

    def update_signal_outcome(
        self,
        signal_id: int,
        outcome_pct: float,
        exit_price: float = None,
        exit_reason: str = None,
    ):
        """Update a signal's outcome when the position closes."""
        conn = self._connect()
        conn.execute(
            "UPDATE strategy_signals_log "
            "SET outcome_pct=?, exit_price=?, exit_reason=? "
            "WHERE id=?",
            (outcome_pct, exit_price, exit_reason, signal_id),
        )
        conn.commit()
        conn.close()

    def mark_signal_executed(self, signal_id: int, entry_price: float):
        """Mark a previously logged signal as executed."""
        conn = self._connect()
        conn.execute(
            "UPDATE strategy_signals_log SET was_executed=1, entry_price=? WHERE id=?",
            (entry_price, signal_id),
        )
        conn.commit()
        conn.close()

    # ── Analytics queries ─────────────────────────────────────────────────

    def get_strategy_summary(self, source: str = None, portfolio_id: int = None) -> List[dict]:
        """
        Get aggregate performance summary per strategy.
        Returns list of dicts with avg metrics across symbols.
        """
        conn = self._connect()
        conditions = []
        params = []
        if source:
            conditions.append("source=?")
            params.append(source)
        if portfolio_id is not None:
            conditions.append("portfolio_id=?")
            params.append(portfolio_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params = tuple(params)
        rows = conn.execute(
            f"SELECT strategy_name, timeframe, source, "
            f"COUNT(*) as num_tests, "
            f"AVG(total_return) as avg_return, "
            f"AVG(sharpe_ratio) as avg_sharpe, "
            f"AVG(win_rate) as avg_win_rate, "
            f"AVG(profit_factor) as avg_profit_factor, "
            f"SUM(total_trades) as total_trades, "
            f"AVG(max_drawdown) as avg_max_drawdown, "
            f"COUNT(CASE WHEN sharpe_ratio >= 0.5 AND win_rate >= 0.4 "
            f"  AND profit_factor >= 1.2 THEN 1 END) as passing "
            f"FROM strategy_performance {where} "
            f"GROUP BY strategy_name, timeframe, source "
            f"ORDER BY avg_sharpe DESC",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_strategy_detail(self, strategy_name: str, source: str = None) -> List[dict]:
        """Get per-symbol performance for a specific strategy."""
        conn = self._connect()
        where = "WHERE strategy_name=?"
        params = [strategy_name]
        if source:
            where += " AND source=?"
            params.append(source)
        rows = conn.execute(
            f"SELECT * FROM strategy_performance {where} "
            f"ORDER BY sharpe_ratio DESC",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_signal_hit_rate(self, strategy_name: str = None, symbol: str = None) -> List[dict]:
        """
        Get signal accuracy stats — how often signals that fired led to profitable outcomes.
        """
        conn = self._connect()
        conditions = []
        params = []
        if strategy_name:
            conditions.append("strategy_name=?")
            params.append(strategy_name)
        if symbol:
            conditions.append("symbol=?")
            params.append(symbol)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = conn.execute(
            f"SELECT strategy_name, symbol, timeframe, action, "
            f"COUNT(*) as total_signals, "
            f"SUM(was_executed) as executed, "
            f"COUNT(outcome_pct) as outcomes_recorded, "
            f"AVG(CASE WHEN outcome_pct IS NOT NULL THEN outcome_pct END) as avg_outcome, "
            f"SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins, "
            f"SUM(CASE WHEN outcome_pct <= 0 THEN 1 ELSE 0 END) as losses, "
            f"AVG(strength) as avg_strength "
            f"FROM strategy_signals_log {where} "
            f"GROUP BY strategy_name, symbol, timeframe, action "
            f"ORDER BY total_signals DESC",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_recent_signals(self, limit: int = 20) -> List[dict]:
        """Get most recent signals across all strategies."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM strategy_signals_log ORDER BY signal_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def compare_backtest_vs_paper(self, strategy_name: str) -> List[dict]:
        """Compare backtest expectations vs paper trading reality for a strategy."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT strategy_name, symbol, timeframe, source, "
            "total_return, sharpe_ratio, win_rate, profit_factor, total_trades "
            "FROM strategy_performance "
            "WHERE strategy_name=? "
            "ORDER BY symbol, source",
            (strategy_name,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def format_strategy_report(tracker: StrategyTracker) -> str:
    """Format a human-readable strategy performance report."""
    lines = ["# Strategy Performance Report", f"Generated: {datetime.now().isoformat()}", ""]

    summary = tracker.get_strategy_summary()
    if summary:
        lines.append("## Strategy Summary")
        lines.append(f"{'Strategy':<22} {'TF':>4} {'Source':>9} {'Tests':>5} {'AvgRet':>8} {'AvgSharpe':>9} {'AvgWR':>6} {'Pass':>5}")
        lines.append("-" * 75)
        for s in summary:
            lines.append(
                f"{s['strategy_name']:<22} {s['timeframe']:>4} {s['source']:>9} "
                f"{s['num_tests']:>5} {s['avg_return']:>+7.1%} {s['avg_sharpe']:>9.2f} "
                f"{s['avg_win_rate']:>5.0%} {s['passing']:>5}"
            )
        lines.append("")

    signals = tracker.get_signal_hit_rate()
    if signals:
        lines.append("## Signal Hit Rate")
        lines.append(f"{'Strategy':<22} {'Symbol':<12} {'Action':>6} {'Signals':>8} {'Exec':>5} {'AvgOut':>8} {'W/L':>6}")
        lines.append("-" * 75)
        for s in signals:
            outcomes = s["outcomes_recorded"] or 0
            avg_out = f"{s['avg_outcome']:>+7.1%}" if s["avg_outcome"] is not None else "    n/a"
            wl = f"{s['wins'] or 0}/{s['losses'] or 0}" if outcomes else "n/a"
            lines.append(
                f"{s['strategy_name']:<22} {s['symbol']:<12} {s['action']:>6} "
                f"{s['total_signals']:>8} {s['executed']:>5} {avg_out} {wl:>6}"
            )

    return "\n".join(lines)


if __name__ == "__main__":
    tracker = StrategyTracker()
    print(format_strategy_report(tracker))
