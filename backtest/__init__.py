"""
Edoras Backtesting Library
~~~~~~~~~~~~~~~~~~~~~~~~~

Public API:
    from backtest import run_backtest, compare_strategies, generate_report
    from backtest import STRATEGY_REGISTRY
"""

from .strategies import Strategy, STRATEGY_REGISTRY
from .metrics import Trade, BacktestMetrics, BacktestResult, calculate_metrics
from .engine import Backtester
from .compare import compare_strategies, ComparisonResult
from .report import generate_report, generate_comparison_report
from .catalogue import StrategyCatalogue
from .deployer import sync_registry, apply_template, swap_strategy, get_strategy_id_map
from .validation import (
    anchored_walk_forward, cost_sensitivity, holdout_gate,
    OOSResult, WalkForwardResult, CostSensitivityResult,
)


def run_backtest(
    strategy_name: str = None,
    strategy: Strategy = None,
    symbol: str = "BTC-USD",
    timeframe: str = "1d",
    start_date: str = "2025-04-01",
    end_date: str = "2026-03-01",
    db_path: str = None,
    initial_capital: float = None,
    use_risk_management: bool = True,
) -> BacktestResult:
    """Convenience function to run a single backtest.

    Provide either strategy_name (looked up from registry) or a strategy instance.
    """
    if strategy is None:
        if strategy_name is None:
            raise ValueError("Provide strategy_name or strategy instance")
        cls = STRATEGY_REGISTRY.get(strategy_name)
        if cls is None:
            available = ", ".join(STRATEGY_REGISTRY.keys())
            raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {available}")
        strategy = cls()

    from config import DB_PATH as DEFAULT_DB, PAPER_INITIAL_CAPITAL
    bt_kwargs = {}
    bt_kwargs["db_path"] = db_path or DEFAULT_DB
    bt_kwargs["initial_capital"] = initial_capital or PAPER_INITIAL_CAPITAL

    bt = Backtester(**bt_kwargs)
    return bt.run(strategy, symbol, timeframe, start_date, end_date, use_risk_management)


__all__ = [
    "run_backtest",
    "compare_strategies",
    "generate_report",
    "generate_comparison_report",
    "Backtester",
    "Strategy",
    "STRATEGY_REGISTRY",
    "Trade",
    "BacktestMetrics",
    "BacktestResult",
    "ComparisonResult",
    "calculate_metrics",
    "StrategyCatalogue",
    "sync_registry",
    "apply_template",
    "swap_strategy",
    "get_strategy_id_map",
    "anchored_walk_forward",
    "cost_sensitivity",
    "holdout_gate",
    "OOSResult",
    "WalkForwardResult",
    "CostSensitivityResult",
]
