"""
Strategy Catalogue — Persistent record of backtest results for strategy selection.

Stores results in SQLite so we can:
  - Track which strategies work on which symbols/timeframes/regimes
  - Rank strategies by risk-adjusted returns
  - Select winning strategies for new portfolio construction
  - Monitor strategy decay over time
"""

import json
import os
import sqlite3
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .metrics import BacktestResult, BacktestMetrics
from .strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

# Default to the main Edoras DB
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "crypto_data.db")


def _ensure_tables(conn: sqlite3.Connection):
    """Create catalogue tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS strategy_catalogue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '1d',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            final_value REAL NOT NULL,

            -- Key metrics (denormalized for fast queries)
            total_return REAL,
            annualized_return REAL,
            sharpe_ratio REAL,
            sortino_ratio REAL,
            calmar_ratio REAL,
            max_drawdown REAL,
            win_rate REAL,
            profit_factor REAL,
            total_trades INTEGER,
            buy_hold_return REAL,
            expectancy REAL,
            avg_holding_days REAL,
            exposure_pct REAL,
            recovery_factor REAL,
            serenity_ratio REAL,

            -- Full metrics JSON for detailed access
            metrics_json TEXT,
            parameters_json TEXT,

            -- Metadata
            catalogued_at TEXT NOT NULL DEFAULT (datetime('now')),
            tags TEXT,  -- comma-separated tags: "winner", "regime:bull", etc.
            notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_cat_strategy ON strategy_catalogue(strategy_name);
        CREATE INDEX IF NOT EXISTS idx_cat_symbol ON strategy_catalogue(symbol);
        CREATE INDEX IF NOT EXISTS idx_cat_sharpe ON strategy_catalogue(sharpe_ratio DESC);

        CREATE TABLE IF NOT EXISTS portfolio_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            strategy_allocations_json TEXT NOT NULL,
            -- JSON: [{"strategy": "X", "symbol": "Y", "weight": 0.2, "timeframe": "1d"}, ...]
            selection_criteria TEXT,  -- how strategies were chosen
            expected_sharpe REAL,
            expected_return REAL,
            expected_max_dd REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 1
        );
    """)


class StrategyCatalogue:
    """Persistent catalogue of backtest results for strategy ranking and portfolio construction."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        _ensure_tables(self.conn)

    def record(self, result: BacktestResult, tags: str = "", notes: str = "") -> int:
        """Record a backtest result in the catalogue. Returns the row ID."""
        m = result.metrics
        metrics_dict = {
            k: v for k, v in vars(m).items()
            if not k.startswith("_") and k != "monthly_returns"
        }
        metrics_dict["monthly_returns"] = m.monthly_returns

        row_id = self.conn.execute("""
            INSERT INTO strategy_catalogue (
                strategy_name, symbol, timeframe, start_date, end_date,
                initial_capital, final_value,
                total_return, annualized_return, sharpe_ratio, sortino_ratio,
                calmar_ratio, max_drawdown, win_rate, profit_factor,
                total_trades, buy_hold_return, expectancy, avg_holding_days,
                exposure_pct, recovery_factor, serenity_ratio,
                metrics_json, parameters_json, tags, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.strategy_name, result.symbol, result.timeframe,
            result.start_date, result.end_date,
            result.initial_capital, result.final_value,
            m.total_return, m.annualized_return, m.sharpe_ratio, m.sortino_ratio,
            m.calmar_ratio, m.max_drawdown, m.win_rate, m.profit_factor,
            m.total_trades, m.buy_hold_return, m.expectancy, m.avg_holding_days,
            m.exposure_pct, m.recovery_factor, m.serenity_ratio,
            json.dumps(metrics_dict), json.dumps(result.parameters),
            tags, notes,
        )).lastrowid
        self.conn.commit()
        return row_id

    def record_comparison(self, results: List[BacktestResult], tags: str = "") -> List[int]:
        """Record multiple results from a comparison run."""
        return [self.record(r, tags=tags) for r in results]

    def rank(
        self,
        metric: str = "sharpe_ratio",
        min_trades: int = 3,
        symbol: str = None,
        timeframe: str = None,
        limit: int = 20,
    ) -> List[dict]:
        """Rank strategies by a given metric. Returns list of dicts."""
        where = ["total_trades >= ?"]
        params = [min_trades]

        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if timeframe:
            where.append("timeframe = ?")
            params.append(timeframe)

        where_clause = " AND ".join(where)
        params.append(limit)

        rows = self.conn.execute(f"""
            SELECT strategy_name, symbol, timeframe, start_date, end_date,
                   total_return, annualized_return, sharpe_ratio, sortino_ratio,
                   calmar_ratio, max_drawdown, win_rate, profit_factor,
                   total_trades, buy_hold_return, expectancy, catalogued_at, tags
            FROM strategy_catalogue
            WHERE {where_clause}
            ORDER BY {metric} DESC
            LIMIT ?
        """, params).fetchall()

        return [dict(r) for r in rows]

    def best_per_symbol(self, metric: str = "sharpe_ratio", min_trades: int = 3) -> List[dict]:
        """Get the best strategy for each symbol."""
        rows = self.conn.execute(f"""
            SELECT sc.*
            FROM strategy_catalogue sc
            INNER JOIN (
                SELECT symbol, MAX({metric}) as best
                FROM strategy_catalogue
                WHERE total_trades >= ?
                GROUP BY symbol
            ) best ON sc.symbol = best.symbol AND sc.{metric} = best.best
            WHERE sc.total_trades >= ?
            ORDER BY sc.{metric} DESC
        """, (min_trades, min_trades)).fetchall()

        return [dict(r) for r in rows]

    def winners(
        self,
        min_sharpe: float = 0.5,
        min_trades: int = 3,
        max_drawdown: float = -0.20,
    ) -> List[dict]:
        """Get strategies that meet minimum quality thresholds."""
        rows = self.conn.execute("""
            SELECT strategy_name, symbol, timeframe, start_date, end_date,
                   total_return, annualized_return, sharpe_ratio, sortino_ratio,
                   max_drawdown, win_rate, profit_factor, total_trades,
                   buy_hold_return, expectancy, catalogued_at
            FROM strategy_catalogue
            WHERE sharpe_ratio >= ?
              AND total_trades >= ?
              AND max_drawdown >= ?
            ORDER BY sharpe_ratio DESC
        """, (min_sharpe, min_trades, max_drawdown)).fetchall()

        return [dict(r) for r in rows]

    def build_portfolio_template(
        self,
        name: str,
        description: str = "",
        min_sharpe: float = 0.0,
        min_trades: int = 3,
        max_drawdown: float = -0.30,
        max_positions: int = 8,
        equal_weight: bool = True,
    ) -> dict:
        """Build a portfolio template from the best catalogued strategies.

        Selects the top strategy per symbol and allocates weights.
        """
        candidates = self.best_per_symbol(metric="sharpe_ratio", min_trades=min_trades)

        # Filter by quality
        qualified = [
            c for c in candidates
            if c["sharpe_ratio"] >= min_sharpe
            and c["max_drawdown"] >= max_drawdown
            and c["total_trades"] >= min_trades
        ]

        # Take top N by Sharpe
        qualified = sorted(qualified, key=lambda x: x["sharpe_ratio"], reverse=True)[:max_positions]

        if not qualified:
            logger.warning("No strategies meet the quality criteria")
            return {}

        # Allocate weights
        if equal_weight:
            weight = round(1.0 / len(qualified), 4)
            allocations = [
                {
                    "strategy": q["strategy_name"],
                    "symbol": q["symbol"],
                    "timeframe": q["timeframe"],
                    "weight": weight,
                    "sharpe": q["sharpe_ratio"],
                    "return": q["total_return"],
                    "max_dd": q["max_drawdown"],
                }
                for q in qualified
            ]
        else:
            # Sharpe-weighted allocation
            total_sharpe = sum(max(q["sharpe_ratio"], 0.01) for q in qualified)
            allocations = [
                {
                    "strategy": q["strategy_name"],
                    "symbol": q["symbol"],
                    "timeframe": q["timeframe"],
                    "weight": round(max(q["sharpe_ratio"], 0.01) / total_sharpe, 4),
                    "sharpe": q["sharpe_ratio"],
                    "return": q["total_return"],
                    "max_dd": q["max_drawdown"],
                }
                for q in qualified
            ]

        expected_sharpe = sum(a["sharpe"] * a["weight"] for a in allocations)
        expected_return = sum(a["return"] * a["weight"] for a in allocations)
        expected_max_dd = min(a["max_dd"] for a in allocations)

        # Store template
        self.conn.execute("""
            INSERT OR REPLACE INTO portfolio_templates
            (name, description, strategy_allocations_json, selection_criteria,
             expected_sharpe, expected_return, expected_max_dd, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            name, description, json.dumps(allocations),
            json.dumps({
                "min_sharpe": min_sharpe,
                "min_trades": min_trades,
                "max_drawdown": max_drawdown,
                "max_positions": max_positions,
                "equal_weight": equal_weight,
            }),
            expected_sharpe, expected_return, expected_max_dd,
        ))
        self.conn.commit()

        template = {
            "name": name,
            "allocations": allocations,
            "expected_sharpe": expected_sharpe,
            "expected_return": expected_return,
            "expected_max_dd": expected_max_dd,
            "positions": len(allocations),
        }
        return template

    def get_template(self, name: str) -> Optional[dict]:
        """Retrieve a portfolio template by name."""
        row = self.conn.execute(
            "SELECT * FROM portfolio_templates WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["allocations"] = json.loads(d["strategy_allocations_json"])
        return d

    def list_templates(self) -> List[dict]:
        """List all portfolio templates."""
        rows = self.conn.execute(
            "SELECT name, description, expected_sharpe, expected_return, expected_max_dd, "
            "created_at, is_active FROM portfolio_templates ORDER BY expected_sharpe DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def build_covariance_portfolio(
        self,
        name: str,
        results: "List[BacktestResult]",
        description: str = "",
        max_positions: int = 8,
        min_sharpe: float = 0.0,
        min_trades: int = 5,
    ) -> dict:
        """
        Build a portfolio template using covariance-aware allocation.

        Instead of treating each strategy-symbol pair independently,
        this uses the equity curves to build a return covariance matrix
        and solves for maximum Sharpe (mean-variance optimal) weights.

        Falls back to inverse-vol if covariance is singular.
        """
        import numpy as np

        # Filter to valid results with equity curves
        valid = [
            r for r in results
            if r.metrics.sharpe_ratio >= min_sharpe
            and r.metrics.total_trades >= min_trades
            and len(r.equity_curve) > 30
        ]
        if not valid:
            logger.warning("No valid results for covariance portfolio")
            return {}

        # Take best per symbol (avoid double-counting)
        best_by_symbol = {}
        for r in sorted(valid, key=lambda x: x.metrics.sharpe_ratio, reverse=True):
            if r.symbol not in best_by_symbol:
                best_by_symbol[r.symbol] = r
        valid = list(best_by_symbol.values())[:max_positions]

        if len(valid) < 2:
            # Can't build covariance with <2 assets; fall back to equal weight
            logger.info("< 2 assets, using equal weight")
            weight = round(1.0 / len(valid), 4)
            allocations = [{
                "strategy": r.strategy_name, "symbol": r.symbol,
                "timeframe": r.timeframe, "weight": weight,
                "sharpe": r.metrics.sharpe_ratio, "return": r.metrics.total_return,
                "max_dd": r.metrics.max_drawdown,
            } for r in valid]
        else:
            # Build return matrix — align all equity curves to common dates
            returns_dict = {}
            for r in valid:
                key = f"{r.strategy_name}|{r.symbol}"
                eq_returns = r.equity_curve.pct_change().dropna()
                returns_dict[key] = eq_returns

            returns_df = pd.DataFrame(returns_dict).dropna()

            if len(returns_df) < 30:
                logger.warning("Insufficient overlapping data for covariance, using inverse-vol")
                vols = [returns_df[c].std() for c in returns_df.columns]
                inv_vols = [1.0 / max(v, 1e-6) for v in vols]
                total = sum(inv_vols)
                weights = [iv / total for iv in inv_vols]
            else:
                mu = returns_df.mean().values * 365  # annualized
                cov = returns_df.cov().values * 365

                # Max-Sharpe via analytical solution (long-only, no constraints)
                try:
                    cov_inv = np.linalg.inv(cov)
                    ones = np.ones(len(mu))
                    raw_w = cov_inv @ mu
                    # Enforce long-only: zero out negative weights, re-normalize
                    raw_w = np.maximum(raw_w, 0)
                    total_w = raw_w.sum()
                    if total_w > 0:
                        weights = (raw_w / total_w).tolist()
                    else:
                        # Fallback: inverse-vol
                        vols = np.sqrt(np.diag(cov))
                        inv_vols = 1.0 / np.maximum(vols, 1e-6)
                        weights = (inv_vols / inv_vols.sum()).tolist()
                except np.linalg.LinAlgError:
                    logger.warning("Singular covariance matrix, using inverse-vol")
                    vols = np.sqrt(np.diag(cov))
                    inv_vols = 1.0 / np.maximum(vols, 1e-6)
                    weights = (inv_vols / inv_vols.sum()).tolist()

            allocations = []
            for r, w in zip(valid, weights):
                allocations.append({
                    "strategy": r.strategy_name,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "weight": round(w, 4),
                    "sharpe": r.metrics.sharpe_ratio,
                    "return": r.metrics.total_return,
                    "max_dd": r.metrics.max_drawdown,
                })

        expected_sharpe = sum(a["sharpe"] * a["weight"] for a in allocations)
        expected_return = sum(a["return"] * a["weight"] for a in allocations)
        expected_max_dd = min(a["max_dd"] for a in allocations)

        self.conn.execute("""
            INSERT OR REPLACE INTO portfolio_templates
            (name, description, strategy_allocations_json, selection_criteria,
             expected_sharpe, expected_return, expected_max_dd, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            name, description, json.dumps(allocations),
            json.dumps({"method": "covariance_aware", "max_positions": max_positions,
                        "min_sharpe": min_sharpe, "min_trades": min_trades}),
            expected_sharpe, expected_return, expected_max_dd,
        ))
        self.conn.commit()

        return {
            "name": name,
            "method": "covariance_aware",
            "allocations": allocations,
            "expected_sharpe": expected_sharpe,
            "expected_return": expected_return,
            "expected_max_dd": expected_max_dd,
            "positions": len(allocations),
        }

    def summary(self) -> dict:
        """Quick summary of catalogue contents."""
        row = self.conn.execute("""
            SELECT COUNT(*) as entries,
                   COUNT(DISTINCT strategy_name) as strategies,
                   COUNT(DISTINCT symbol) as symbols,
                   AVG(sharpe_ratio) as avg_sharpe,
                   MAX(sharpe_ratio) as best_sharpe,
                   AVG(total_return) as avg_return
            FROM strategy_catalogue
        """).fetchone()
        return dict(row)

    def close(self):
        self.conn.close()
