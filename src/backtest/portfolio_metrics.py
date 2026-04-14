"""
Portfolio-level backtest result and metrics.

PortfolioBacktestResult holds equity curves and metrics at both the
portfolio and per-symbol level, plus a return correlation matrix
and P&L contribution breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from .metrics import Trade, BacktestMetrics


@dataclass
class PortfolioBacktestResult:
    """Result of a multi-asset portfolio backtest."""

    # Configuration
    assignments: List[dict] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 0.0
    final_value: float = 0.0

    # Portfolio-level
    equity_curve: pd.Series = field(default_factory=pd.Series)
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    trades: List[Trade] = field(default_factory=list)

    # Per-symbol breakdown
    per_symbol_equity: Dict[str, pd.Series] = field(default_factory=dict)
    per_symbol_metrics: Dict[str, BacktestMetrics] = field(default_factory=dict)

    # Cross-asset analysis
    correlation_matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    contribution: Dict[str, float] = field(default_factory=dict)  # symbol -> P&L USD

    def summary_table(self) -> List[dict]:
        """Summary row per symbol for tabular display."""
        rows = []
        for symbol, m in self.per_symbol_metrics.items():
            contrib = self.contribution.get(symbol, 0.0)
            rows.append({
                "symbol": symbol,
                "return": f"{m.total_return:.2%}",
                "sharpe": f"{m.sharpe_ratio:.2f}",
                "max_dd": f"{m.max_drawdown:.2%}",
                "win_rate": f"{m.win_rate:.1%}",
                "trades": m.total_trades,
                "contribution": f"${contrib:.2f}",
            })
        return rows
