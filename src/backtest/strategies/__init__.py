"""
Strategy base class and registry for the Edoras backtesting library.
"""

from typing import Dict, List, Type

import pandas as pd


class Strategy:
    """Base class for backtesting strategies."""

    name: str = "BaseStrategy"
    required_symbols: List[str] = []  # extra symbols needed (e.g. pairs trading)
    required_timeframes: List[str] = []  # extra timeframes to pre-load
    required_references: List[str] = []  # reference symbols (e.g. "^VIX")

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        """
        Given a DataFrame (with indicators) up to the current bar,
        return a list of signal dicts:
            [{"action": "BUY"/"SELL", "weight": 0-1, "reason": "..."}]
        """
        raise NotImplementedError

    def generate_signals_multi(
        self, data: Dict[str, pd.DataFrame], portfolio: dict
    ) -> List[dict]:
        """Override for multi-symbol strategies (e.g. pairs trading).

        data: {symbol: DataFrame} for primary + required_symbols.
        Default delegates to generate_signals() with the first DataFrame.
        """
        return self.generate_signals(list(data.values())[0], portfolio)

    def generate_signals_ctx(self, ctx: "StrategyContext") -> List[dict]:
        """Override for strategies that need multi-timeframe or reference data.

        ctx: StrategyContext with primary_df, timeframe_data, reference_data.
        Default delegates to generate_signals(ctx.primary_df, ctx.portfolio).

        Import StrategyContext at call time to avoid circular imports.
        """
        return self.generate_signals(ctx.primary_df, ctx.portfolio)

    @property
    def needs_context(self) -> bool:
        """True if this strategy needs rich context (multi-TF or reference data)."""
        return bool(self.required_timeframes) or bool(self.required_references)

    def fit(self, df: pd.DataFrame) -> None:
        """Optional: fit parameters on in-sample data. Called by walk-forward.

        Override in strategies that can optimize params on training data.
        Default is a no-op — strategies with fixed parameters need not implement this.
        """
        pass

    def get_parameters(self) -> dict:
        return {}

    @classmethod
    def describe(cls) -> str:
        """One-line description for reports."""
        return cls.__doc__.strip().split("\n")[0] if cls.__doc__ else cls.name


STRATEGY_REGISTRY: Dict[str, Type[Strategy]] = {}


def register_strategy(cls: Type[Strategy]) -> Type[Strategy]:
    """Decorator: registers a Strategy subclass by its .name attribute."""
    STRATEGY_REGISTRY[cls.name] = cls
    return cls


# Import all strategy modules to trigger registration
from . import score_based, macd_cross, adx_trend, bollinger, multi_signal  # noqa: E402, F401
from . import tsmom, pairs_trading, regime_aware, bear_defensive  # noqa: E402, F401
