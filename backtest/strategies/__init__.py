"""
Strategy base class and registry for the Edoras backtesting library.
"""

from typing import Dict, List, Type

import pandas as pd


class Strategy:
    """Base class for backtesting strategies."""

    name: str = "BaseStrategy"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        """
        Given a DataFrame (with indicators) up to the current bar,
        return a list of signal dicts:
            [{"action": "BUY"/"SELL", "weight": 0-1, "reason": "..."}]
        """
        raise NotImplementedError

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
from . import tsmom, pairs_trading, regime_aware  # noqa: E402, F401
