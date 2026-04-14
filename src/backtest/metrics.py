"""
Comprehensive performance metrics for backtesting results.
"""

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass
class Trade:
    symbol: str
    side: str  # "BUY" or "SELL"
    price: float
    quantity: float
    timestamp: "datetime"
    reason: str = ""


@dataclass
class BacktestMetrics:
    # ── Returns ──────────────────────────────────────────────────────────
    total_return: float = 0.0
    annualized_return: float = 0.0
    buy_hold_return: float = 0.0

    # ── Risk-adjusted ────────────────────────────────────────────────────
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    serenity_ratio: float = 0.0

    # ── Drawdown ─────────────────────────────────────────────────────────
    max_drawdown: float = 0.0
    max_drawdown_duration_days: float = 0.0
    avg_drawdown: float = 0.0
    ulcer_index: float = 0.0

    # ── Trade quality ────────────────────────────────────────────────────
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    payoff_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_holding_days: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # ── Exposure ─────────────────────────────────────────────────────────
    exposure_pct: float = 0.0
    recovery_factor: float = 0.0
    tail_ratio: float = 0.0

    # ── Monthly breakdown ────────────────────────────────────────────────
    monthly_returns: Dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    price_series: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    parameters: dict = field(default_factory=dict)


def calculate_metrics(
    equity_curve: pd.Series,
    trades: List[Trade],
    initial_capital: float,
    price_series: pd.Series = None,
) -> BacktestMetrics:
    """Calculate comprehensive performance metrics."""
    m = BacktestMetrics()
    if equity_curve.empty:
        return m

    final = equity_curve.iloc[-1]
    m.total_return = (final - initial_capital) / initial_capital
    m.total_trades = len([t for t in trades if t.side == "BUY"])

    # Buy-and-hold benchmark
    if price_series is not None and len(price_series) >= 2:
        m.buy_hold_return = (price_series.iloc[-1] - price_series.iloc[0]) / price_series.iloc[0]

    # ── Daily returns ────────────────────────────────────────────────────
    daily = equity_curve.resample("D").last().dropna()
    returns = daily.pct_change().dropna()

    if len(returns) > 1:
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = max(days / 365.25, 0.01)
        m.annualized_return = (final / initial_capital) ** (1 / years) - 1

        mean_r = returns.mean()
        std_r = returns.std()
        if std_r > 0:
            m.sharpe_ratio = mean_r / std_r * np.sqrt(365)

        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            m.sortino_ratio = mean_r / downside.std() * np.sqrt(365)

        # ── Drawdown analysis ────────────────────────────────────────────
        cum = (1 + returns).cumprod()
        running_max = cum.expanding().max()
        dd = (cum - running_max) / running_max
        m.max_drawdown = dd.min()

        # Drawdown duration
        in_dd = dd < 0
        if in_dd.any():
            dd_groups = (~in_dd).cumsum()
            dd_lengths = in_dd.groupby(dd_groups).sum()
            m.max_drawdown_duration_days = float(dd_lengths.max())

        # Average drawdown (mean of trough per drawdown episode)
        dd_troughs = []
        current_trough = 0.0
        for d in dd:
            if d < 0:
                current_trough = min(current_trough, d)
            elif current_trough < 0:
                dd_troughs.append(current_trough)
                current_trough = 0.0
        if current_trough < 0:
            dd_troughs.append(current_trough)
        m.avg_drawdown = float(np.mean(dd_troughs)) if dd_troughs else 0.0

        # Ulcer index: sqrt(mean(drawdown^2))
        m.ulcer_index = float(np.sqrt(np.mean(dd ** 2)))

        # Serenity ratio: annualized return / ulcer index
        if m.ulcer_index > 0:
            m.serenity_ratio = m.annualized_return / m.ulcer_index

        if m.max_drawdown != 0:
            m.calmar_ratio = m.annualized_return / abs(m.max_drawdown)
            m.recovery_factor = m.total_return / abs(m.max_drawdown)

        # Tail ratio: |95th percentile / 5th percentile|
        p5 = np.percentile(returns, 5)
        p95 = np.percentile(returns, 95)
        if p5 != 0:
            m.tail_ratio = abs(p95 / p5)

        # Monthly returns
        monthly = daily.resample("ME").last().pct_change().dropna()
        m.monthly_returns = {dt.strftime("%Y-%m"): float(ret) for dt, ret in monthly.items()}

    # ── Trade-level metrics ──────────────────────────────────────────────
    buy_trades = [t for t in trades if t.side == "BUY"]
    sell_trades = [t for t in trades if t.side == "SELL"]

    if buy_trades and sell_trades:
        wins = []
        losses = []
        holding_days = []
        outcomes = []  # ordered list of True (win) / False (loss)
        days_in_market = 0.0

        for bt in buy_trades:
            sells_after = [s for s in sell_trades if s.timestamp > bt.timestamp]
            if sells_after:
                st = sells_after[0]
                pnl = (st.price - bt.price) / bt.price
                hold = (st.timestamp - bt.timestamp).total_seconds() / 86400
                holding_days.append(hold)
                days_in_market += hold
                if pnl > 0:
                    wins.append(pnl)
                    outcomes.append(True)
                else:
                    losses.append(pnl)
                    outcomes.append(False)

        total = len(wins) + len(losses)
        if total > 0:
            m.win_rate = len(wins) / total
            m.avg_win = float(np.mean(wins)) if wins else 0.0
            m.avg_loss = float(np.mean(losses)) if losses else 0.0
            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0.001
            m.profit_factor = gross_profit / gross_loss

            # Expectancy: expected return per trade
            m.expectancy = (m.win_rate * m.avg_win) + ((1 - m.win_rate) * m.avg_loss)

            # Payoff ratio: avg win / abs(avg loss)
            if m.avg_loss != 0:
                m.payoff_ratio = abs(m.avg_win / m.avg_loss)

            # Consecutive streaks
            max_w = max_l = cur_w = cur_l = 0
            for w in outcomes:
                if w:
                    cur_w += 1; cur_l = 0
                    max_w = max(max_w, cur_w)
                else:
                    cur_l += 1; cur_w = 0
                    max_l = max(max_l, cur_l)
            m.max_consecutive_wins = max_w
            m.max_consecutive_losses = max_l

        if holding_days:
            m.avg_holding_days = float(np.mean(holding_days))

        # Exposure: fraction of time in market
        if len(equity_curve) > 0:
            total_days = (equity_curve.index[-1] - equity_curve.index[0]).days
            if total_days > 0:
                m.exposure_pct = days_in_market / total_days

    return m
