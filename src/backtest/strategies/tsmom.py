"""
Time-Series Momentum (TSMOM) Strategy.

Based on Moskowitz, Ooi & Pedersen (2012) and Huang, Sangiorgi & Urquhart (2024).
Uses 12-month cumulative returns scaled by inverse volatility, targeting
15% annualized risk per asset. Weekly rebalancing cadence.

Key insight: position size is proportional to signal strength and inversely
proportional to recent volatility — this mitigates the crash risk inherent
in raw momentum strategies.
"""

import logging

import numpy as np
import pandas as pd

from . import Strategy, register_strategy

logger = logging.getLogger(__name__)


@register_strategy
class TSMOMStrategy(Strategy):
    """Time-series momentum with inverse-volatility scaling."""

    name = "TSMOM"

    def __init__(
        self,
        lookback: int = 252,       # ~12 months of daily bars
        vol_window: int = 60,      # rolling volatility window
        target_vol: float = 0.15,  # 15% annualized risk target
        signal_threshold: float = 0.0,  # min cumulative return to trigger
    ):
        self.lookback = lookback
        self.vol_window = vol_window
        self.target_vol = target_vol
        self.signal_threshold = signal_threshold

    def get_parameters(self) -> dict:
        return {
            "lookback": self.lookback,
            "vol_window": self.vol_window,
            "target_vol": self.target_vol,
            "signal_threshold": self.signal_threshold,
        }

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> list:
        symbol = portfolio.get("symbol", "?")

        if len(df) < self.lookback + 1:
            logger.info(f"[TSMOM/{symbol}] Silent — insufficient data ({len(df)} bars, need {self.lookback+1})")
            return []

        close = df["close"]
        signals = []

        # Cumulative return over lookback period
        cum_return = (close.iloc[-1] / close.iloc[-self.lookback]) - 1

        # Rolling volatility (annualized)
        returns = close.pct_change().dropna()
        if len(returns) < self.vol_window:
            logger.info(f"[TSMOM/{symbol}] Silent — insufficient return data ({len(returns)} < {self.vol_window})")
            return []
        recent_vol = returns.iloc[-self.vol_window:].std() * np.sqrt(365)

        if recent_vol <= 0:
            logger.info(f"[TSMOM/{symbol}] Silent — zero volatility")
            return []

        # Inverse-vol position sizing: scale to target vol
        raw_weight = self.target_vol / recent_vol
        raw_weight = min(raw_weight, 1.0)  # cap at 100%

        # MACD confirmation filter (use existing indicator)
        bar = df.iloc[-1]
        macd_hist = bar.get("macd_histogram", 0) or 0
        rsi = bar.get("rsi_14", 50) or 50
        pos_qty = portfolio["position_qty"]

        if cum_return > self.signal_threshold and pos_qty == 0:
            # Positive momentum — go long
            weight = raw_weight * 0.8
            if macd_hist > 0:
                weight = min(weight * 1.2, 0.95)
            if rsi > 75:
                weight *= 0.6

            signals.append({
                "action": "BUY",
                "weight": round(weight, 4),
                "reason": (
                    f"TSMOM BUY: {self.lookback}d return={cum_return:.2%} "
                    f"vol={recent_vol:.2%} scaled_wt={raw_weight:.2f} "
                    f"RSI={rsi:.0f} MACD_h={macd_hist:.4f}"
                ),
            })

        elif cum_return < -self.signal_threshold and pos_qty > 0:
            # Negative momentum — exit
            signals.append({
                "action": "SELL",
                "weight": 1.0,
                "reason": (
                    f"TSMOM SELL: {self.lookback}d return={cum_return:.2%} "
                    f"vol={recent_vol:.2%} RSI={rsi:.0f}"
                ),
            })

        if not signals:
            blockers = []
            if cum_return <= self.signal_threshold and pos_qty == 0:
                blockers.append(f"ret={cum_return:.2%}<=threshold({self.signal_threshold:.2%})")
            if cum_return >= -self.signal_threshold and pos_qty > 0:
                blockers.append(f"ret={cum_return:.2%}>-threshold (hold)")
            if pos_qty > 0 and cum_return > self.signal_threshold:
                blockers.append(f"already holding (qty={pos_qty:.6g})")
            logger.info(
                f"[TSMOM/{symbol}] Silent — {self.lookback}d_ret={cum_return:.2%} "
                f"vol={recent_vol:.2%} pos={'held' if pos_qty > 0 else 'none'} "
                f"| {', '.join(blockers)}"
            )

        return signals


@register_strategy
class TSMOMShortLookbackStrategy(Strategy):
    """TSMOM with shorter 3-month lookback for faster regime adaptation."""

    name = "TSMOM_3M"

    def __init__(self):
        self._inner = TSMOMStrategy(
            lookback=63,            # ~3 months
            vol_window=30,
            target_vol=0.15,
            signal_threshold=0.02,  # require 2% min return
        )

    def get_parameters(self) -> dict:
        return self._inner.get_parameters()

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> list:
        return self._inner.generate_signals(df, portfolio)
