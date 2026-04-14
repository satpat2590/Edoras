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
from typing import List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import DB_PATH, PAPER_INITIAL_CAPITAL, PAPER_TRANSACTION_COST
from data.indicator_calculator import calculate_all_indicators

from .strategies import Strategy
from .metrics import Trade, BacktestResult, BacktestMetrics, calculate_metrics
from .signals import Signal
from .risk_config import RiskConfig
from .fee_model import FeeModel

logger = logging.getLogger(__name__)


class _KellyTracker:
    """Tracks win/loss statistics during a backtest for proper Kelly sizing.

    Uses exponentially-weighted stats so the Kelly fraction adapts to
    changing market regimes rather than being dominated by early trades.
    """

    def __init__(self, min_trades: int = 10, decay: float = 0.95):
        self.min_trades = min_trades
        self.decay = decay
        self._wins: List[float] = []  # positive returns
        self._losses: List[float] = []  # negative returns (stored as positive)
        self._total = 0

    def record(self, entry_price: float, exit_price: float) -> None:
        """Record a completed trade's return."""
        if entry_price <= 0:
            return
        pnl_pct = (exit_price - entry_price) / entry_price
        self._total += 1
        if pnl_pct > 0:
            self._wins.append(pnl_pct)
        else:
            self._losses.append(abs(pnl_pct))

    @property
    def kelly_fraction(self) -> float:
        """Compute Kelly fraction: f* = (p*b - q) / b.

        Returns a conservative 0.25 until min_trades are observed.
        Uses exponential weighting on recent trades.
        """
        if self._total < self.min_trades:
            return 0.25  # conservative default

        # Exponentially-weighted win rate and payoff
        n = len(self._wins) + len(self._losses)
        if n == 0:
            return 0.25

        # Build combined sequence with decay weights
        weights_w = [self.decay ** (len(self._wins) - 1 - i) for i in range(len(self._wins))]
        weights_l = [self.decay ** (len(self._losses) - 1 - i) for i in range(len(self._losses))]

        total_w = sum(weights_w) + sum(weights_l)
        if total_w == 0:
            return 0.25

        p = sum(weights_w) / total_w  # weighted win rate
        q = 1.0 - p

        avg_win = (
            sum(w * r for w, r in zip(weights_w, self._wins)) / sum(weights_w)
            if weights_w else 0.001
        )
        avg_loss = (
            sum(w * r for w, r in zip(weights_l, self._losses)) / sum(weights_l)
            if weights_l else 0.001
        )

        if avg_loss <= 0:
            return 0.25

        b = avg_win / avg_loss  # payoff ratio
        f_star = (p * b - q) / b if b > 0 else 0

        # Clamp to [0, 0.5] — never bet more than half-Kelly
        return max(0.0, min(f_star * 0.5, 0.5))


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
        risk_config: Optional[RiskConfig] = None,
        fee_model: Optional[FeeModel] = None,
    ):
        self.db_path = db_path
        self.initial_capital = initial_capital
        self.sizing_mode = sizing_mode  # signal_weight | inverse_vol | kelly
        self.target_annual_vol = target_annual_vol  # for inverse_vol mode
        self.vol_lookback = vol_lookback
        self.kelly_fraction = kelly_fraction  # fractional Kelly (half-Kelly default)
        self._kelly_tracker = _KellyTracker() if sizing_mode == "kelly" else None

        # Risk config: explicit > config globals
        self.risk_config = risk_config or RiskConfig.from_config_globals()
        self.max_position_pct = max_position_pct

        # Fee model: explicit > legacy flat params > auto-resolve per symbol in run()
        self._explicit_fee_model = fee_model
        self._legacy_transaction_cost = transaction_cost
        self._legacy_slippage_bps = slippage_bps

    def _resolve_fee_model(self, symbol: str) -> FeeModel:
        """Resolve fee model for a symbol.

        Priority: explicit fee_model > legacy flat params (if non-default) > asset profile.
        """
        if self._explicit_fee_model is not None:
            return self._explicit_fee_model
        # If caller passed non-default transaction_cost, honor it (backward compat)
        if (
            self._legacy_transaction_cost != PAPER_TRANSACTION_COST
            or self._legacy_slippage_bps != 0.0
        ):
            return FeeModel.flat(self._legacy_transaction_cost, self._legacy_slippage_bps)
        # Auto-resolve from asset class profile
        try:
            return FeeModel.from_asset_profile(symbol)
        except Exception:
            return FeeModel.flat(self._legacy_transaction_cost, self._legacy_slippage_bps)

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
            # Adaptive Kelly: uses running win/loss stats from _KellyTracker
            kf = self._kelly_tracker.kelly_fraction if self._kelly_tracker else self.kelly_fraction
            raw = capital * signal_weight * kf

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

    def _load_auxiliary_data(
        self, strategy: Strategy, symbol: str, start_date: str, end_date: str,
    ) -> dict:
        """Pre-load multi-timeframe and reference data for strategies that need it.

        Returns: {"timeframes": {tf: df}, "references": {sym: df}}
        """
        from .context import StrategyContext

        result = {"timeframes": {}, "references": {}}

        if not getattr(strategy, "needs_context", False):
            return result

        for tf in getattr(strategy, "required_timeframes", []):
            df = self._load_data(symbol, tf, start_date, end_date)
            if not df.empty:
                result["timeframes"][tf] = df

        for ref_sym in getattr(strategy, "required_references", []):
            # Load reference at daily resolution (most common for VIX etc.)
            df = self._load_data(ref_sym, "1d", start_date, end_date)
            if not df.empty:
                result["references"][ref_sym] = df

        return result

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

        fee = self._resolve_fee_model(symbol)
        rc = self.risk_config

        # Pre-load auxiliary data for strategies that need context
        use_context = getattr(strategy, "needs_context", False)
        aux_data = self._load_auxiliary_data(strategy, symbol, start_date, end_date) if use_context else None

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
                if price <= entry_price * (1 - rc.stop_loss_pct):
                    exit_price = fee.apply_slippage(price, "SELL")
                    proceeds = position_qty * exit_price - fee.compute_fee(position_qty * exit_price)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", exit_price, position_qty, ts, "stop_loss"))
                    if self._kelly_tracker:
                        self._kelly_tracker.record(entry_price, exit_price)
                    position_qty = 0.0
                    entry_price = 0.0
                    continue

                # Trailing stop
                gain = (price - entry_price) / entry_price
                if gain >= rc.trailing_stop_activation:
                    atr = bar.get("atr_14")
                    if atr and not pd.isna(atr) and atr > 0:
                        trail = high_watermark - 2 * atr
                    else:
                        trail = high_watermark * (1 - rc.trailing_stop_pct)
                    trail = max(trail, entry_price * 1.001)  # breakeven floor
                    if price <= trail:
                        exit_price = fee.apply_slippage(price, "SELL")
                        proceeds = position_qty * exit_price - fee.compute_fee(position_qty * exit_price)
                        capital += proceeds
                        trades.append(Trade(symbol, "SELL", exit_price, position_qty, ts, "trailing_stop"))
                        if self._kelly_tracker:
                            self._kelly_tracker.record(entry_price, exit_price)
                        position_qty = 0.0
                        entry_price = 0.0
                        continue

                # Take profit scale-out
                for j, (threshold, sell_pct) in enumerate(rc.take_profit_levels):
                    if j in partial_exits_hit:
                        continue
                    if gain >= threshold:
                        sell_qty = position_qty * sell_pct
                        exit_price = fee.apply_slippage(price, "SELL")
                        proceeds = sell_qty * exit_price - fee.compute_fee(sell_qty * exit_price)
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

            if use_context and aux_data:
                from .context import StrategyContext
                current_ts = int(bar["timestamp"])
                # Slice auxiliary data up to current timestamp (prevent lookahead)
                tf_data = {
                    tf: aux_df[aux_df["timestamp"] <= current_ts]
                    for tf, aux_df in aux_data["timeframes"].items()
                }
                ref_data = {
                    ref: aux_df[aux_df["timestamp"] <= current_ts]
                    for ref, aux_df in aux_data["references"].items()
                }
                ctx = StrategyContext(
                    primary_df=window,
                    symbol=symbol,
                    timeframe=timeframe,
                    portfolio=portfolio,
                    timeframe_data=tf_data,
                    reference_data=ref_data,
                    current_timestamp=current_ts,
                )
                raw_signals = strategy.generate_signals_ctx(ctx)
            else:
                raw_signals = strategy.generate_signals(window, portfolio)

            for raw_sig in raw_signals:
                sig = raw_sig if isinstance(raw_sig, Signal) else Signal.from_dict(raw_sig)

                if sig.action == "BUY" and position_qty == 0 and capital > 10:
                    buy_amount = self._compute_position_size(capital, sig.weight, window)
                    fill_price = fee.apply_slippage(price, "BUY")
                    trade_fee = fee.compute_fee(buy_amount)
                    cost = buy_amount + trade_fee
                    if cost <= capital and buy_amount > 0:
                        qty = buy_amount / fill_price
                        capital -= cost
                        position_qty += qty
                        entry_price = fill_price
                        high_watermark = fill_price
                        partial_exits_hit = []
                        trades.append(Trade(symbol, "BUY", fill_price, qty, ts, sig.reason))

                elif sig.action in ("SELL", "CLOSE") and position_qty > 0:
                    fill_price = fee.apply_slippage(price, "SELL")
                    notional = position_qty * fill_price
                    proceeds = notional - fee.compute_fee(notional)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", fill_price, position_qty, ts, sig.reason))
                    if self._kelly_tracker:
                        self._kelly_tracker.record(entry_price, fill_price)
                    position_qty = 0.0
                    entry_price = 0.0

                elif sig.action == "REDUCE" and position_qty > 0 and sig.target_position_pct is not None:
                    target_qty = position_qty * sig.target_position_pct
                    sell_qty = position_qty - target_qty
                    if sell_qty > 0:
                        fill_price = fee.apply_slippage(price, "SELL")
                        notional = sell_qty * fill_price
                        proceeds = notional - fee.compute_fee(notional)
                        capital += proceeds
                        position_qty = target_qty
                        trades.append(Trade(symbol, "SELL", fill_price, sell_qty, ts, sig.reason))

        # Close any remaining position at last price
        if position_qty > 0:
            last_price = df.iloc[-1]["close"]
            notional = position_qty * last_price
            proceeds = notional - fee.compute_fee(notional)
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

            # Fit strategy on in-sample data if it supports fitting
            train_start_str = w_start.strftime("%Y-%m-%d")
            train_end_str = train_end.strftime("%Y-%m-%d")
            if hasattr(strategy, "fit") and callable(strategy.fit):
                train_df = self._load_data(symbol, timeframe, train_start_str, train_end_str)
                if not train_df.empty:
                    strategy.fit(train_df)

            oos_start = train_end.strftime("%Y-%m-%d")
            oos_end = min(w_end, end_dt).strftime("%Y-%m-%d")

            result = self.run(strategy, symbol, timeframe, oos_start, oos_end)
            result.strategy_name = f"{strategy.name}_split{i+1}"
            results.append(result)

        return results
