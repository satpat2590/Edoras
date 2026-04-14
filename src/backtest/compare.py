"""
Multi-strategy / multi-symbol comparison runner.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .engine import Backtester
from .metrics import BacktestResult
from .strategies import Strategy, STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class ComparisonResult:
    """Holds results across multiple strategies and/or symbols."""
    results: List[BacktestResult] = field(default_factory=list)

    @property
    def by_strategy(self) -> Dict[str, List[BacktestResult]]:
        out: Dict[str, List[BacktestResult]] = {}
        for r in self.results:
            out.setdefault(r.strategy_name, []).append(r)
        return out

    @property
    def by_symbol(self) -> Dict[str, List[BacktestResult]]:
        out: Dict[str, List[BacktestResult]] = {}
        for r in self.results:
            out.setdefault(r.symbol, []).append(r)
        return out

    def best_by_metric(self, metric: str = "sharpe_ratio") -> Optional[BacktestResult]:
        """Return the result with the highest value for the given metric."""
        if not self.results:
            return None
        return max(self.results, key=lambda r: getattr(r.metrics, metric, 0))

    def summary_table(self) -> List[dict]:
        """Return a list of dicts suitable for tabular display."""
        rows = []
        for r in self.results:
            m = r.metrics
            rows.append({
                "strategy": r.strategy_name,
                "symbol": r.symbol,
                "return": f"{m.total_return:.2%}",
                "ann_return": f"{m.annualized_return:.2%}",
                "sharpe": f"{m.sharpe_ratio:.2f}",
                "sortino": f"{m.sortino_ratio:.2f}",
                "max_dd": f"{m.max_drawdown:.2%}",
                "calmar": f"{m.calmar_ratio:.2f}",
                "win_rate": f"{m.win_rate:.1%}",
                "trades": m.total_trades,
                "profit_factor": f"{m.profit_factor:.2f}",
                "buy_hold": f"{m.buy_hold_return:.2%}",
            })
        return rows


def compare_strategies(
    symbols: List[str],
    strategies: Optional[List[Strategy]] = None,
    timeframe: str = "1d",
    start_date: str = "2025-04-01",
    end_date: str = "2026-03-01",
    db_path: str = None,
    initial_capital: float = None,
) -> ComparisonResult:
    """Run all strategies against all symbols and collect results."""
    from config import DB_PATH as DEFAULT_DB, PAPER_INITIAL_CAPITAL

    bt_kwargs = {}
    if db_path:
        bt_kwargs["db_path"] = db_path
    else:
        bt_kwargs["db_path"] = DEFAULT_DB
    if initial_capital:
        bt_kwargs["initial_capital"] = initial_capital
    else:
        bt_kwargs["initial_capital"] = PAPER_INITIAL_CAPITAL

    bt = Backtester(**bt_kwargs)

    if strategies is None:
        strategies = [cls() for cls in STRATEGY_REGISTRY.values()]

    comparison = ComparisonResult()
    for symbol in symbols:
        for strat in strategies:
            logger.info(f"Running {strat.name} on {symbol} ({start_date} -> {end_date})")
            result = bt.run(strat, symbol, timeframe, start_date, end_date)
            comparison.results.append(result)

    return comparison
