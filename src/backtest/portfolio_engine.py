"""
Multi-asset portfolio backtester.

Runs multiple strategies on multiple symbols simultaneously, sharing
capital across a portfolio with per-symbol fee/risk resolution.

Usage:
    from backtest import PortfolioBacktester, STRATEGY_REGISTRY

    bt = PortfolioBacktester(initial_capital=10_000)
    result = bt.run(
        assignments=[
            {"strategy": STRATEGY_REGISTRY["TSMOM"](), "symbol": "BTC-USD", "weight": 0.4, "timeframe": "1d"},
            {"strategy": STRATEGY_REGISTRY["BollingerReversion"](), "symbol": "ETH-USD", "weight": 0.3, "timeframe": "1d"},
            {"strategy": STRATEGY_REGISTRY["MultiSignal"](), "symbol": "LINK-USD", "weight": 0.3, "timeframe": "1d"},
        ],
        start_date="2025-06-01",
        end_date="2026-03-01",
    )
"""

import logging
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import DB_PATH, PAPER_INITIAL_CAPITAL
from data.indicator_calculator import calculate_all_indicators

from .fee_model import FeeModel
from .metrics import BacktestMetrics, Trade, calculate_metrics
from .portfolio_metrics import PortfolioBacktestResult
from .portfolio_state import PortfolioState
from .risk_config import RiskConfig
from .signals import Signal
from .strategies import Strategy

logger = logging.getLogger(__name__)


class PortfolioBacktester:
    """Multi-asset portfolio backtester with per-symbol fee/risk resolution."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        initial_capital: float = PAPER_INITIAL_CAPITAL,
        sizing_mode: str = "signal_weight",
        target_annual_vol: float = 0.15,
        vol_lookback: int = 60,
        kelly_fraction: float = 0.5,
    ):
        self.db_path = db_path
        self.initial_capital = initial_capital
        self.sizing_mode = sizing_mode
        self.target_annual_vol = target_annual_vol
        self.vol_lookback = vol_lookback
        self.kelly_fraction = kelly_fraction

    def _load_data(
        self, symbol: str, timeframe: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Load OHLCV data and compute indicators for a single symbol."""
        conn = sqlite3.connect(self.db_path)
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())
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
        df = df[df["timestamp"] >= start_ts].reset_index(drop=True)
        return df

    def _compute_position_size(
        self,
        available_capital: float,
        signal_weight: float,
        df_window: pd.DataFrame,
        max_position_pct: float,
    ) -> float:
        """Compute dollar amount for a position."""
        if self.sizing_mode == "inverse_vol":
            returns = df_window["close"].pct_change().dropna()
            if len(returns) >= self.vol_lookback:
                recent_vol = returns.iloc[-self.vol_lookback :].std()
                annual_vol = recent_vol * np.sqrt(365)
                if annual_vol > 0:
                    vol_scalar = self.target_annual_vol / annual_vol
                    raw = available_capital * min(vol_scalar, 1.0) * signal_weight
                else:
                    raw = available_capital * signal_weight
            else:
                raw = available_capital * signal_weight
        elif self.sizing_mode == "kelly":
            raw = available_capital * signal_weight * self.kelly_fraction
        else:
            raw = available_capital * signal_weight

        raw = min(raw, available_capital * max_position_pct)
        raw = min(raw, available_capital * 0.95)
        return max(raw, 0)

    def run(
        self,
        assignments: List[dict],
        start_date: str = "2025-06-01",
        end_date: str = "2026-03-01",
        use_risk_management: bool = True,
    ) -> PortfolioBacktestResult:
        """Run a multi-asset portfolio backtest.

        assignments: list of dicts, each with:
            - "strategy": Strategy instance
            - "symbol": str
            - "timeframe": str (default "1d")
            - "weight": float (target allocation, 0-1, should sum to ~1.0)
        """
        if not assignments:
            return PortfolioBacktestResult(
                initial_capital=self.initial_capital,
                final_value=self.initial_capital,
                start_date=start_date,
                end_date=end_date,
            )

        # Normalize weights
        total_weight = sum(a.get("weight", 1.0 / len(assignments)) for a in assignments)
        for a in assignments:
            a.setdefault("weight", 1.0 / len(assignments))
            a.setdefault("timeframe", "1d")
            a["_norm_weight"] = a["weight"] / total_weight if total_weight > 0 else 0

        # Load data for all symbols
        symbol_data: Dict[str, pd.DataFrame] = {}
        symbol_fees: Dict[str, FeeModel] = {}
        symbol_risk: Dict[str, RiskConfig] = {}

        all_symbols = set()
        for a in assignments:
            sym = a["symbol"]
            all_symbols.add(sym)
            # Also load required_symbols for multi-symbol strategies
            strategy: Strategy = a["strategy"]
            for extra_sym in getattr(strategy, "required_symbols", []):
                all_symbols.add(extra_sym)

        for sym in all_symbols:
            # Find timeframe — use first assignment that references this symbol
            tf = "1d"
            for a in assignments:
                if a["symbol"] == sym:
                    tf = a["timeframe"]
                    break

            df = self._load_data(sym, tf, start_date, end_date)
            if df.empty or len(df) < 30:
                logger.warning(f"Insufficient data for {sym}/{tf}: {len(df)} rows, skipping")
                continue
            symbol_data[sym] = df
            symbol_fees[sym] = FeeModel.from_asset_profile(sym)
            try:
                symbol_risk[sym] = RiskConfig.from_asset_profile(sym)
            except Exception:
                symbol_risk[sym] = RiskConfig.from_config_globals()

        # Filter assignments to symbols with data
        active_assignments = [a for a in assignments if a["symbol"] in symbol_data]
        if not active_assignments:
            return PortfolioBacktestResult(
                assignments=assignments,
                initial_capital=self.initial_capital,
                final_value=self.initial_capital,
                start_date=start_date,
                end_date=end_date,
            )

        # Build unified timestamp index (intersection of all symbols)
        ts_sets = [set(symbol_data[a["symbol"]]["timestamp"].values) for a in active_assignments]
        common_ts = sorted(set.intersection(*ts_sets))
        if len(common_ts) < 30:
            logger.warning(f"Only {len(common_ts)} common timestamps across symbols")
            return PortfolioBacktestResult(
                assignments=assignments,
                initial_capital=self.initial_capital,
                final_value=self.initial_capital,
                start_date=start_date,
                end_date=end_date,
            )

        # Index DataFrames by timestamp for fast lookup
        sym_indexed: Dict[str, pd.DataFrame] = {}
        for sym, df in symbol_data.items():
            sym_indexed[sym] = df.set_index("timestamp")

        # Initialize state
        state = PortfolioState(capital=self.initial_capital)
        all_trades: List[Trade] = []
        per_symbol_trades: Dict[str, List[Trade]] = {a["symbol"]: [] for a in active_assignments}
        equity_values: List[float] = []
        equity_dates: List[datetime] = []
        per_symbol_equity: Dict[str, List[float]] = {a["symbol"]: [] for a in active_assignments}
        price_values: List[float] = []  # portfolio-weighted price for benchmark

        for bar_idx, ts in enumerate(common_ts):
            if bar_idx < 30:
                continue

            # Current prices
            prices: Dict[str, float] = {}
            for sym in symbol_data:
                if ts in sym_indexed[sym].index:
                    prices[sym] = sym_indexed[sym].loc[ts, "close"]
                    if isinstance(prices[sym], pd.Series):
                        prices[sym] = prices[sym].iloc[0]

            portfolio_nav = state.nav(prices)
            dt = datetime.utcfromtimestamp(int(ts))
            equity_values.append(portfolio_nav)
            equity_dates.append(dt)

            # Track per-symbol equity (position value + proportional cash)
            for a in active_assignments:
                sym = a["symbol"]
                pos = state.positions.get(sym)
                pos_val = (pos.quantity * prices.get(sym, 0.0)) if pos and pos.is_open else 0.0
                cash_share = state.capital * a["_norm_weight"]
                per_symbol_equity[sym].append(pos_val + cash_share)

            # Weighted price for buy-hold benchmark
            bp = sum(
                prices.get(a["symbol"], 0.0) * a["_norm_weight"]
                for a in active_assignments
                if a["symbol"] in prices
            )
            price_values.append(bp)

            # ── Process each symbol-strategy assignment ───────────────
            # Collect all signals first, then execute SELLs before BUYs
            sell_signals: List[tuple] = []  # (assignment, Signal, price, fee, rc)
            buy_signals: List[tuple] = []

            for a in active_assignments:
                sym = a["symbol"]
                strategy: Strategy = a["strategy"]
                fee = symbol_fees.get(sym, FeeModel.flat(0.001))
                rc = symbol_risk.get(sym, RiskConfig.from_config_globals())
                price = prices.get(sym)
                if price is None:
                    continue

                pos = state.get_position(sym)

                # ── Risk management exits ─────────────────────────────
                if use_risk_management and pos.is_open:
                    exit_signal = self._check_risk_exit(pos, price, rc, sym_indexed[sym], ts)
                    if exit_signal:
                        sell_signals.append((a, exit_signal, price, fee, rc))
                        continue  # skip strategy signals if risk exit triggered

                    if price > pos.high_watermark:
                        pos.high_watermark = price

                # ── Strategy signals ──────────────────────────────────
                # Build window up to current bar
                sym_df = symbol_data[sym]
                mask = sym_df["timestamp"] <= ts
                window = sym_df[mask]
                if len(window) < 30:
                    continue

                portfolio_dict = state.to_strategy_portfolio(sym)

                # Multi-symbol strategies
                if getattr(strategy, "required_symbols", []):
                    multi_data = {sym: window}
                    for extra_sym in strategy.required_symbols:
                        if extra_sym in symbol_data:
                            extra_df = symbol_data[extra_sym]
                            extra_window = extra_df[extra_df["timestamp"] <= ts]
                            multi_data[extra_sym] = extra_window
                    raw_signals = strategy.generate_signals_multi(multi_data, portfolio_dict)
                else:
                    raw_signals = strategy.generate_signals(window, portfolio_dict)

                for raw_sig in raw_signals:
                    sig = raw_sig if isinstance(raw_sig, Signal) else Signal.from_dict(raw_sig)
                    if sig.action in ("SELL", "CLOSE", "REDUCE"):
                        sell_signals.append((a, sig, price, fee, rc))
                    elif sig.action == "BUY":
                        buy_signals.append((a, sig, price, fee, rc))

            # ── Execute SELLs first (free capital) ────────────────────
            for a, sig, price, fee, rc in sell_signals:
                sym = a["symbol"]
                pos = state.get_position(sym)
                if not pos.is_open:
                    continue
                dt = datetime.utcfromtimestamp(int(ts))

                if sig.action == "REDUCE" and sig.target_position_pct is not None:
                    target_qty = pos.quantity * sig.target_position_pct
                    sell_qty = pos.quantity - target_qty
                else:
                    sell_qty = pos.quantity

                if sell_qty <= 0:
                    continue

                fill_price = fee.apply_slippage(price, "SELL")
                notional = sell_qty * fill_price
                proceeds = notional - fee.compute_fee(notional)
                state.capital += proceeds
                pos.quantity -= sell_qty
                trade = Trade(sym, "SELL", fill_price, sell_qty, dt, sig.reason)
                all_trades.append(trade)
                per_symbol_trades[sym].append(trade)

                if pos.quantity <= 0:
                    pos.reset()

            # ── Execute BUYs (weight-descending order) ────────────────
            buy_signals.sort(key=lambda x: x[1].weight, reverse=True)
            for a, sig, price, fee, rc in buy_signals:
                sym = a["symbol"]
                pos = state.get_position(sym)
                if pos.is_open:
                    continue  # already holding
                if state.capital <= 10:
                    continue

                dt = datetime.utcfromtimestamp(int(ts))

                # Capital allocated to this assignment
                allocated = state.capital * a["_norm_weight"]
                # Don't exceed total available capital
                allocated = min(allocated, state.capital * 0.95)

                sym_df = symbol_data[sym]
                window = sym_df[sym_df["timestamp"] <= ts]
                buy_amount = self._compute_position_size(
                    allocated, sig.weight, window, rc.max_position_pct
                )
                fill_price = fee.apply_slippage(price, "BUY")
                trade_fee = fee.compute_fee(buy_amount)
                cost = buy_amount + trade_fee

                if cost <= state.capital and buy_amount > 0:
                    qty = buy_amount / fill_price
                    state.capital -= cost
                    pos.quantity = qty
                    pos.entry_price = fill_price
                    pos.high_watermark = fill_price
                    pos.partial_exits_hit = []
                    trade = Trade(sym, "BUY", fill_price, qty, dt, sig.reason)
                    all_trades.append(trade)
                    per_symbol_trades[sym].append(trade)

        # ── Close remaining positions at last bar ─────────────────────
        last_ts = common_ts[-1]
        last_dt = datetime.utcfromtimestamp(int(last_ts))
        for a in active_assignments:
            sym = a["symbol"]
            pos = state.get_position(sym)
            if pos.is_open:
                price = prices.get(sym, 0.0)
                fee = symbol_fees.get(sym, FeeModel.flat(0.001))
                notional = pos.quantity * price
                proceeds = notional - fee.compute_fee(notional)
                state.capital += proceeds
                trade = Trade(sym, "SELL", price, pos.quantity, last_dt, "end_of_backtest")
                all_trades.append(trade)
                per_symbol_trades[sym].append(trade)
                pos.reset()

        # ── Compute metrics ───────────────────────────────────────────
        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates))
        price_series = pd.Series(price_values, index=pd.DatetimeIndex(equity_dates))
        portfolio_metrics = calculate_metrics(equity_curve, all_trades, self.initial_capital, price_series)

        per_sym_metrics: Dict[str, BacktestMetrics] = {}
        per_sym_equity_series: Dict[str, pd.Series] = {}
        contribution: Dict[str, float] = {}

        for a in active_assignments:
            sym = a["symbol"]
            sym_equity = per_symbol_equity.get(sym, [])
            if sym_equity:
                sym_series = pd.Series(sym_equity, index=pd.DatetimeIndex(equity_dates))
                per_sym_equity_series[sym] = sym_series
                sym_trades = per_symbol_trades.get(sym, [])
                initial_alloc = self.initial_capital * a["_norm_weight"]
                per_sym_metrics[sym] = calculate_metrics(
                    sym_series, sym_trades, initial_alloc,
                    pd.Series(dtype=float),  # no single-symbol price benchmark
                )
                # P&L contribution
                final_sym = sym_equity[-1] if sym_equity else initial_alloc
                contribution[sym] = final_sym - initial_alloc

        # Correlation matrix from per-symbol equity returns
        corr_matrix = pd.DataFrame()
        if len(per_sym_equity_series) >= 2:
            returns_df = pd.DataFrame({
                sym: s.pct_change().dropna()
                for sym, s in per_sym_equity_series.items()
            })
            corr_matrix = returns_df.corr()

        return PortfolioBacktestResult(
            assignments=[
                {"strategy": a["strategy"].name, "symbol": a["symbol"],
                 "weight": a["weight"], "timeframe": a["timeframe"]}
                for a in active_assignments
            ],
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=state.capital,
            equity_curve=equity_curve,
            metrics=portfolio_metrics,
            trades=all_trades,
            per_symbol_equity=per_sym_equity_series,
            per_symbol_metrics=per_sym_metrics,
            correlation_matrix=corr_matrix,
            contribution=contribution,
        )

    def _check_risk_exit(
        self,
        pos: "PositionState",
        price: float,
        rc: RiskConfig,
        sym_df: pd.DataFrame,
        current_ts: int,
    ) -> Optional[Signal]:
        """Check risk management rules, return exit Signal or None."""
        if not pos.is_open:
            return None

        gain = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0

        # Stop loss
        if price <= pos.entry_price * (1 - rc.stop_loss_pct):
            return Signal(action="SELL", weight=1.0, reason="stop_loss")

        # Trailing stop
        if gain >= rc.trailing_stop_activation:
            atr = None
            if current_ts in sym_df.index:
                row = sym_df.loc[current_ts]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[0]
                atr_val = row.get("atr_14") if hasattr(row, "get") else None
                if atr_val is not None and not pd.isna(atr_val) and atr_val > 0:
                    atr = atr_val

            if atr:
                trail = pos.high_watermark - 2 * atr
            else:
                trail = pos.high_watermark * (1 - rc.trailing_stop_pct)
            trail = max(trail, pos.entry_price * 1.001)

            if price <= trail:
                return Signal(action="SELL", weight=1.0, reason="trailing_stop")

        # Take profit scale-out
        for j, (threshold, sell_pct) in enumerate(rc.take_profit_levels):
            if j in pos.partial_exits_hit:
                continue
            if gain >= threshold:
                pos.partial_exits_hit.append(j)
                return Signal(
                    action="REDUCE" if sell_pct < 1.0 else "SELL",
                    weight=1.0,
                    reason=f"take_profit_L{j + 1}",
                    target_position_pct=1.0 - sell_pct if sell_pct < 1.0 else 0.0,
                )

        return None
