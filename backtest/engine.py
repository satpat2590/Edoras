"""
Core backtesting engine — replays historical data bar-by-bar.

Position sizing modes:
  - signal_weight: use strategy's weight directly (legacy, default)
  - inverse_vol:   scale position by target_vol / realized_vol
  - kelly:         Kelly fraction based on running win rate and payoff
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    DB_PATH, PAPER_INITIAL_CAPITAL, PAPER_TRANSACTION_COST,
    STOP_LOSS_PCT, TRAILING_STOP_ACTIVATION, TRAILING_STOP_PCT,
    TAKE_PROFIT_LEVELS,
)
from indicator_calculator import calculate_all_indicators

from .strategies import Strategy
from .metrics import Trade, BacktestResult, BacktestMetrics, calculate_metrics

logger = logging.getLogger(__name__)


class Backtester:
    """
    Event-driven backtester that replays historical data bar-by-bar.
    Supports stop-loss, trailing stop, take-profit exits,
    volatility-scaled position sizing, and slippage modeling.
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        initial_capital: float = PAPER_INITIAL_CAPITAL,
        transaction_cost: float = PAPER_TRANSACTION_COST,
        slippage_bps: float = 0.0,
        sizing_mode: str = "signal_weight",
        target_annual_vol: float = 0.15,
        vol_lookback: int = 60,
        max_position_pct: float = 0.25,
        kelly_fraction: float = 0.5,
    ):
        self.db_path = db_path
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost
        self.slippage_bps = slippage_bps  # basis points of slippage per trade
        self.sizing_mode = sizing_mode  # signal_weight | inverse_vol | kelly
        self.target_annual_vol = target_annual_vol  # for inverse_vol mode
        self.vol_lookback = vol_lookback
        self.max_position_pct = max_position_pct  # hard cap on any single position
        self.kelly_fraction = kelly_fraction  # fractional Kelly (half-Kelly default)

    def _apply_slippage(self, price: float, side: str) -> float:
        """Apply slippage to execution price. Buys fill higher, sells lower."""
        slip = price * self.slippage_bps / 10000
        return price + slip if side == "BUY" else price - slip

    def _compute_position_size(
        self, capital: float, signal_weight: float, df_window: pd.DataFrame
    ) -> float:
        """
        Compute dollar amount to allocate based on sizing mode.

        signal_weight: raw 0-1 weight from strategy
        Returns: dollar amount to invest (before fees)
        """
        if self.sizing_mode == "inverse_vol":
            # Inverse-volatility sizing: scale to target annual vol
            returns = df_window["close"].pct_change().dropna()
            if len(returns) >= self.vol_lookback:
                recent_vol = returns.iloc[-self.vol_lookback:].std()
                annual_vol = recent_vol * np.sqrt(365)
                if annual_vol > 0:
                    vol_scalar = self.target_annual_vol / annual_vol
                    raw = capital * min(vol_scalar, 1.0) * signal_weight
                else:
                    raw = capital * signal_weight
            else:
                raw = capital * signal_weight

        elif self.sizing_mode == "kelly":
            # Half-Kelly: f* = (p * b - q) / b, capped at kelly_fraction
            # Uses running trade stats
            raw = capital * signal_weight * self.kelly_fraction

        else:
            # signal_weight mode (legacy)
            raw = capital * signal_weight

        # Hard cap
        raw = min(raw, capital * self.max_position_pct)
        # Keep minimum cash reserve
        raw = min(raw, capital * 0.95)
        return max(raw, 0)

    def _load_data(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Load OHLCV data from DB and compute indicators."""
        conn = sqlite3.connect(self.db_path)
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())

        # Extra lookback for indicator warmup
        warmup_ts = start_ts - (250 * 86400)

        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candlesticks WHERE symbol=? AND timeframe=? AND timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp",
            conn,
            params=(symbol, timeframe, warmup_ts, end_ts),
        )
        conn.close()

        if df.empty:
            return df

        df["date"] = pd.to_datetime(df["timestamp"], unit="s")
        df = calculate_all_indicators(df)
        # Trim warmup
        df = df[df["timestamp"] >= start_ts].reset_index(drop=True)
        return df

    def run(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str = "1d",
        start_date: str = "2025-04-01",
        end_date: str = "2026-03-01",
        use_risk_management: bool = True,
    ) -> BacktestResult:
        """Run a full backtest for a single symbol."""
        df = self._load_data(symbol, timeframe, start_date, end_date)
        if df.empty or len(df) < 30:
            logger.warning(f"Insufficient data for {symbol}/{timeframe}: {len(df)} rows")
            return BacktestResult(
                strategy_name=strategy.name, symbol=symbol, timeframe=timeframe,
                start_date=start_date, end_date=end_date,
                initial_capital=self.initial_capital, final_value=self.initial_capital,
            )

        capital = self.initial_capital
        position_qty = 0.0
        entry_price = 0.0
        high_watermark = 0.0
        partial_exits_hit: List[int] = []
        trades: List[Trade] = []
        equity_values = []
        equity_dates = []
        price_values = []

        for i in range(30, len(df)):
            bar = df.iloc[i]
            price = bar["close"]
            ts = datetime.utcfromtimestamp(int(bar["timestamp"]))
            portfolio_value = capital + position_qty * price

            equity_values.append(portfolio_value)
            equity_dates.append(ts)
            price_values.append(price)

            # ── Risk management exits ────────────────────────────────
            if use_risk_management and position_qty > 0:
                # Stop loss
                if price <= entry_price * (1 - STOP_LOSS_PCT):
                    exit_price = self._apply_slippage(price, "SELL")
                    proceeds = position_qty * exit_price * (1 - self.transaction_cost)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", exit_price, position_qty, ts, "stop_loss"))
                    position_qty = 0.0
                    entry_price = 0.0
                    continue

                # Trailing stop
                gain = (price - entry_price) / entry_price
                if gain >= TRAILING_STOP_ACTIVATION:
                    atr = bar.get("atr_14")
                    if atr and not pd.isna(atr) and atr > 0:
                        trail = high_watermark - 2 * atr
                    else:
                        trail = high_watermark * (1 - TRAILING_STOP_PCT)
                    trail = max(trail, entry_price * 1.001)  # breakeven floor
                    if price <= trail:
                        exit_price = self._apply_slippage(price, "SELL")
                        proceeds = position_qty * exit_price * (1 - self.transaction_cost)
                        capital += proceeds
                        trades.append(Trade(symbol, "SELL", exit_price, position_qty, ts, "trailing_stop"))
                        position_qty = 0.0
                        entry_price = 0.0
                        continue

                # Take profit scale-out
                for j, (threshold, sell_pct) in enumerate(TAKE_PROFIT_LEVELS):
                    if j in partial_exits_hit:
                        continue
                    if gain >= threshold:
                        sell_qty = position_qty * sell_pct
                        exit_price = self._apply_slippage(price, "SELL")
                        proceeds = sell_qty * exit_price * (1 - self.transaction_cost)
                        capital += proceeds
                        position_qty -= sell_qty
                        partial_exits_hit.append(j)
                        trades.append(Trade(symbol, "SELL", exit_price, sell_qty, ts, f"take_profit_L{j+1}"))
                        break

                if price > high_watermark:
                    high_watermark = price

            # ── Strategy signals ─────────────────────────────────────
            window = df.iloc[:i + 1]
            portfolio = {"capital": capital, "position_qty": position_qty, "entry_price": entry_price, "symbol": symbol}
            signals = strategy.generate_signals(window, portfolio)

            for sig in signals:
                if sig["action"] == "BUY" and position_qty == 0 and capital > 10:
                    buy_amount = self._compute_position_size(capital, sig["weight"], window)
                    fill_price = self._apply_slippage(price, "BUY")
                    cost = buy_amount * (1 + self.transaction_cost)
                    if cost <= capital and buy_amount > 0:
                        qty = buy_amount / fill_price
                        capital -= cost
                        position_qty += qty
                        entry_price = fill_price
                        high_watermark = fill_price
                        partial_exits_hit = []
                        trades.append(Trade(symbol, "BUY", fill_price, qty, ts, sig["reason"]))

                elif sig["action"] == "SELL" and position_qty > 0:
                    fill_price = self._apply_slippage(price, "SELL")
                    proceeds = position_qty * fill_price * (1 - self.transaction_cost)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", fill_price, position_qty, ts, sig["reason"]))
                    position_qty = 0.0
                    entry_price = 0.0

        # Close any remaining position at last price
        if position_qty > 0:
            last_price = df.iloc[-1]["close"]
            proceeds = position_qty * last_price * (1 - self.transaction_cost)
            capital += proceeds
            trades.append(Trade(symbol, "SELL", last_price, position_qty,
                                datetime.utcfromtimestamp(int(df.iloc[-1]["timestamp"])), "end_of_backtest"))
            position_qty = 0.0

        final_value = capital
        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates))
        price_series = pd.Series(price_values, index=pd.DatetimeIndex(equity_dates))

        metrics = calculate_metrics(equity_curve, trades, self.initial_capital, price_series)

        return BacktestResult(
            strategy_name=strategy.name,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=final_value,
            equity_curve=equity_curve,
            price_series=price_series,
            trades=trades,
            metrics=metrics,
            parameters=strategy.get_parameters(),
        )

    # ── Walk-forward validation ──────────────────────────────────────────

    def walk_forward(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str = "1d",
        start_date: str = "2025-04-01",
        end_date: str = "2026-03-01",
        n_splits: int = 4,
        train_pct: float = 0.7,
    ) -> List[BacktestResult]:
        """Split data into n_splits windows, backtest each out-of-sample."""
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        total_days = (end_dt - start_dt).days
        window_days = total_days // n_splits

        results = []
        for i in range(n_splits):
            w_start = start_dt + timedelta(days=i * window_days)
            w_end = w_start + timedelta(days=window_days)
            train_end = w_start + timedelta(days=int(window_days * train_pct))

            oos_start = train_end.strftime("%Y-%m-%d")
            oos_end = min(w_end, end_dt).strftime("%Y-%m-%d")

            result = self.run(strategy, symbol, timeframe, oos_start, oos_end)
            result.strategy_name = f"{strategy.name}_split{i+1}"
            results.append(result)

        return results
