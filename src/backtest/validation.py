"""
Out-of-sample validation framework and cost sensitivity analysis.

Implements:
- Anchored walk-forward: expanding in-sample window, fixed OOS holdout
- Holdout gating: strategies must pass OOS to be deployed
- Cost sensitivity: sweep across fee/slippage levels
- Statistical significance: minimum trade count thresholds
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

import numpy as np
import pandas as pd

from .engine import Backtester
from .metrics import BacktestResult, BacktestMetrics, calculate_metrics
from .strategies import Strategy, STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────

@dataclass
class OOSResult:
    """Out-of-sample validation result for a single strategy-symbol pair."""
    strategy_name: str
    symbol: str
    timeframe: str
    in_sample: BacktestResult
    out_of_sample: BacktestResult
    is_sharpe_ratio: float = 0.0  # in-sample Sharpe
    oos_sharpe_ratio: float = 0.0  # out-of-sample Sharpe
    sharpe_decay: float = 0.0  # (IS - OOS) / IS — how much edge decayed
    passed: bool = False  # meets OOS quality thresholds


@dataclass
class WalkForwardResult:
    """Anchored walk-forward results across multiple folds."""
    strategy_name: str
    symbol: str
    folds: List[OOSResult] = field(default_factory=list)
    avg_oos_sharpe: float = 0.0
    avg_sharpe_decay: float = 0.0
    pct_folds_passed: float = 0.0
    total_oos_trades: int = 0
    combined_oos_return: float = 0.0


@dataclass
class CostSensitivityResult:
    """Results across fee/slippage levels."""
    strategy_name: str
    symbol: str
    base_result: BacktestResult
    fee_results: Dict[float, BacktestResult] = field(default_factory=dict)
    breakeven_fee: float = 0.0  # fee level where Sharpe drops to 0


# ── Anchored Walk-Forward Validation ─────────────────────────────────────

def anchored_walk_forward(
    strategy: Strategy,
    symbol: str,
    timeframe: str = "1d",
    start_date: str = "2023-06-01",
    end_date: str = "2026-03-01",
    oos_months: int = 3,
    n_folds: int = 4,
    min_oos_sharpe: float = 0.0,
    min_oos_trades: int = 3,
    db_path: str = None,
    initial_capital: float = None,
) -> WalkForwardResult:
    """
    Anchored walk-forward: each fold expands the in-sample window
    while keeping a fixed OOS holdout period.

    Fold 1: IS=[start, end-4*oos]  OOS=[end-4*oos, end-3*oos]
    Fold 2: IS=[start, end-3*oos]  OOS=[end-3*oos, end-2*oos]
    Fold 3: IS=[start, end-2*oos]  OOS=[end-2*oos, end-1*oos]
    Fold 4: IS=[start, end-1*oos]  OOS=[end-1*oos, end]

    This avoids lookahead bias and tests on progressively more recent data.
    """
    from config import DB_PATH as DEFAULT_DB, PAPER_INITIAL_CAPITAL

    bt_kwargs = {"db_path": db_path or DEFAULT_DB}
    if initial_capital:
        bt_kwargs["initial_capital"] = initial_capital
    else:
        bt_kwargs["initial_capital"] = PAPER_INITIAL_CAPITAL

    bt = Backtester(**bt_kwargs)

    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    oos_delta = timedelta(days=oos_months * 30)

    folds: List[OOSResult] = []

    for i in range(n_folds):
        # OOS period: counting back from end
        oos_end_dt = end_dt - timedelta(days=i * oos_months * 30)
        oos_start_dt = oos_end_dt - oos_delta

        if oos_start_dt <= start_dt:
            logger.warning(f"Fold {n_folds - i}: OOS start {oos_start_dt.date()} before data start, skipping")
            continue

        is_start = start_date
        is_end = oos_start_dt.strftime("%Y-%m-%d")
        oos_start = is_end
        oos_end = oos_end_dt.strftime("%Y-%m-%d")

        logger.info(f"Fold {n_folds - i}: IS=[{is_start}, {is_end}]  OOS=[{oos_start}, {oos_end}]")

        is_result = bt.run(strategy, symbol, timeframe, is_start, is_end)

        # Fit strategy on in-sample data before OOS evaluation
        if hasattr(strategy, "fit") and callable(strategy.fit):
            train_df = bt._load_data(symbol, timeframe, is_start, is_end)
            if not train_df.empty:
                strategy.fit(train_df)

        oos_result = bt.run(strategy, symbol, timeframe, oos_start, oos_end)

        is_sharpe = is_result.metrics.sharpe_ratio
        oos_sharpe = oos_result.metrics.sharpe_ratio
        decay = (is_sharpe - oos_sharpe) / max(abs(is_sharpe), 0.01) if is_sharpe != 0 else 0

        passed = (
            oos_sharpe >= min_oos_sharpe
            and oos_result.metrics.total_trades >= min_oos_trades
        )

        fold = OOSResult(
            strategy_name=strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            in_sample=is_result,
            out_of_sample=oos_result,
            is_sharpe_ratio=is_sharpe,
            oos_sharpe_ratio=oos_sharpe,
            sharpe_decay=decay,
            passed=passed,
        )
        folds.append(fold)

    # Reverse so folds are in chronological order
    folds.reverse()

    oos_sharpes = [f.oos_sharpe_ratio for f in folds]
    oos_decays = [f.sharpe_decay for f in folds]
    oos_trades = sum(f.out_of_sample.metrics.total_trades for f in folds)
    oos_returns = [f.out_of_sample.metrics.total_return for f in folds]
    passed_count = sum(1 for f in folds if f.passed)

    return WalkForwardResult(
        strategy_name=strategy.name,
        symbol=symbol,
        folds=folds,
        avg_oos_sharpe=float(np.mean(oos_sharpes)) if oos_sharpes else 0.0,
        avg_sharpe_decay=float(np.mean(oos_decays)) if oos_decays else 0.0,
        pct_folds_passed=passed_count / len(folds) if folds else 0.0,
        total_oos_trades=oos_trades,
        combined_oos_return=float(np.prod([1 + r for r in oos_returns]) - 1) if oos_returns else 0.0,
    )


# ── Cost Sensitivity Analysis ────────────────────────────────────────────

def cost_sensitivity(
    strategy: Strategy,
    symbol: str,
    timeframe: str = "1d",
    start_date: str = "2023-06-01",
    end_date: str = "2026-03-01",
    fee_levels: List[float] = None,
    db_path: str = None,
    initial_capital: float = None,
) -> CostSensitivityResult:
    """
    Sweep across transaction cost levels to measure edge robustness.

    Default levels: 0.0%, 0.05%, 0.1%, 0.2%, 0.3%, 0.5%, 1.0%
    These approximate:
      - 0.05%: Coinbase Pro maker fee
      - 0.1%:  Coinbase taker fee (current paper assumption)
      - 0.2%:  taker + typical spread on mid-cap crypto
      - 0.5%:  low-liquidity altcoins (BONK, TROLL)
      - 1.0%:  DEX swap with slippage on thin pools
    """
    from config import DB_PATH as DEFAULT_DB, PAPER_INITIAL_CAPITAL

    if fee_levels is None:
        fee_levels = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.01]

    bt_kwargs = {"db_path": db_path or DEFAULT_DB}
    if initial_capital:
        bt_kwargs["initial_capital"] = initial_capital
    else:
        bt_kwargs["initial_capital"] = PAPER_INITIAL_CAPITAL

    base_result = None
    fee_results = {}
    breakeven_fee = fee_levels[-1]  # default: assume it survives all levels

    for fee in fee_levels:
        bt = Backtester(**{**bt_kwargs, "transaction_cost": fee})
        result = bt.run(strategy, symbol, timeframe, start_date, end_date)
        fee_results[fee] = result

        if fee == 0.0:
            base_result = result

        if result.metrics.sharpe_ratio <= 0 and breakeven_fee == fee_levels[-1]:
            # Interpolate breakeven between this fee and the previous one
            prev_fee = fee_levels[fee_levels.index(fee) - 1] if fee_levels.index(fee) > 0 else 0
            prev_sharpe = fee_results[prev_fee].metrics.sharpe_ratio
            curr_sharpe = result.metrics.sharpe_ratio
            if prev_sharpe > 0 and prev_sharpe != curr_sharpe:
                breakeven_fee = prev_fee + (fee - prev_fee) * prev_sharpe / (prev_sharpe - curr_sharpe)
            else:
                breakeven_fee = fee

    if base_result is None:
        base_result = fee_results[fee_levels[0]]

    return CostSensitivityResult(
        strategy_name=strategy.name,
        symbol=symbol,
        base_result=base_result,
        fee_results=fee_results,
        breakeven_fee=breakeven_fee,
    )


# ── Holdout Gate ─────────────────────────────────────────────────────────

def holdout_gate(
    symbols: List[str],
    strategies: Optional[List[Strategy]] = None,
    timeframe: str = "1d",
    start_date: str = "2023-06-01",
    end_date: str = "2026-03-01",
    holdout_months: int = 3,
    min_oos_sharpe: float = 0.0,
    min_oos_trades: int = 5,
    min_breakeven_fee: float = 0.002,
    db_path: str = None,
    initial_capital: float = None,
) -> List[dict]:
    """
    Gate strategies through OOS validation + cost sensitivity.

    Only strategies that:
    1. Have positive Sharpe on the holdout period
    2. Generate enough trades (statistical significance)
    3. Survive realistic transaction costs (breakeven fee > threshold)

    are approved for deployment.

    Returns list of approved strategy-symbol pairs with full diagnostics.
    """
    from config import DB_PATH as DEFAULT_DB, PAPER_INITIAL_CAPITAL

    if strategies is None:
        strategies = [cls() for cls in STRATEGY_REGISTRY.values()]

    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    holdout_start = (end_dt - timedelta(days=holdout_months * 30)).strftime("%Y-%m-%d")

    approved = []
    rejected = []

    for symbol in symbols:
        for strat in strategies:
            logger.info(f"Gating {strat.name} on {symbol}")

            bt_kwargs = {"db_path": db_path or DEFAULT_DB}
            if initial_capital:
                bt_kwargs["initial_capital"] = initial_capital
            else:
                bt_kwargs["initial_capital"] = PAPER_INITIAL_CAPITAL

            bt = Backtester(**bt_kwargs)

            # In-sample run
            is_result = bt.run(strat, symbol, timeframe, start_date, holdout_start)

            # Out-of-sample run
            oos_result = bt.run(strat, symbol, timeframe, holdout_start, end_date)

            # Cost sensitivity on full period
            cs = cost_sensitivity(
                strat, symbol, timeframe, start_date, end_date,
                db_path=db_path, initial_capital=initial_capital,
            )

            entry = {
                "strategy": strat.name,
                "symbol": symbol,
                "timeframe": timeframe,
                "is_sharpe": is_result.metrics.sharpe_ratio,
                "is_return": is_result.metrics.total_return,
                "is_trades": is_result.metrics.total_trades,
                "oos_sharpe": oos_result.metrics.sharpe_ratio,
                "oos_return": oos_result.metrics.total_return,
                "oos_trades": oos_result.metrics.total_trades,
                "oos_max_dd": oos_result.metrics.max_drawdown,
                "oos_win_rate": oos_result.metrics.win_rate,
                "breakeven_fee": cs.breakeven_fee,
                "sharpe_at_0.2pct": cs.fee_results.get(0.002, oos_result).metrics.sharpe_ratio,
            }

            # Gate criteria
            passes = (
                oos_result.metrics.sharpe_ratio >= min_oos_sharpe
                and oos_result.metrics.total_trades >= min_oos_trades
                and cs.breakeven_fee >= min_breakeven_fee
            )

            entry["approved"] = passes
            if passes:
                reason = []
                if oos_result.metrics.sharpe_ratio < is_result.metrics.sharpe_ratio * 0.5:
                    reason.append("high_sharpe_decay")
                entry["warnings"] = reason
                approved.append(entry)
                logger.info(
                    f"  APPROVED: OOS Sharpe={entry['oos_sharpe']:.2f}, "
                    f"trades={entry['oos_trades']}, breakeven_fee={entry['breakeven_fee']:.4f}"
                )
            else:
                reasons = []
                if oos_result.metrics.sharpe_ratio < min_oos_sharpe:
                    reasons.append(f"oos_sharpe={oos_result.metrics.sharpe_ratio:.2f}<{min_oos_sharpe}")
                if oos_result.metrics.total_trades < min_oos_trades:
                    reasons.append(f"oos_trades={oos_result.metrics.total_trades}<{min_oos_trades}")
                if cs.breakeven_fee < min_breakeven_fee:
                    reasons.append(f"breakeven_fee={cs.breakeven_fee:.4f}<{min_breakeven_fee}")
                entry["rejection_reasons"] = reasons
                rejected.append(entry)
                logger.info(f"  REJECTED: {', '.join(reasons)}")

    logger.info(f"\nGating complete: {len(approved)} approved, {len(rejected)} rejected")
    return approved
