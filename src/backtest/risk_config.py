"""
RiskConfig — configurable risk parameters for backtesting.

Replaces hardcoded imports from config.py globals, allowing per-backtest
and per-asset-class risk parameter customization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class RiskConfig:
    """Risk management parameters for a backtest run."""

    stop_loss_pct: float = 0.10
    trailing_stop_activation: float = 0.05
    trailing_stop_pct: float = 0.05
    take_profit_levels: List[Tuple[float, float]] = field(
        default_factory=lambda: [(0.15, 0.33), (0.20, 0.33), (0.25, 1.0)]
    )
    max_position_pct: float = 0.25

    @classmethod
    def from_config_globals(cls) -> RiskConfig:
        """Load from config.py module-level constants (current default behavior)."""
        from config import (
            STOP_LOSS_PCT,
            TRAILING_STOP_ACTIVATION,
            TRAILING_STOP_PCT,
            TAKE_PROFIT_LEVELS,
            MAX_POSITION_PCT,
        )

        return cls(
            stop_loss_pct=STOP_LOSS_PCT,
            trailing_stop_activation=TRAILING_STOP_ACTIVATION,
            trailing_stop_pct=TRAILING_STOP_PCT,
            take_profit_levels=list(TAKE_PROFIT_LEVELS),
            max_position_pct=MAX_POSITION_PCT,
        )

    @classmethod
    def from_asset_profile(cls, symbol: str) -> RiskConfig:
        """Load from ASSET_CLASS_PROFILES via get_asset_class_profile().

        Falls back to config globals for any keys not in the profile.
        """
        from config import (
            get_asset_class_profile,
            STOP_LOSS_PCT,
            TRAILING_STOP_ACTIVATION,
            TRAILING_STOP_PCT,
            TAKE_PROFIT_LEVELS,
            MAX_POSITION_PCT,
        )

        profile = get_asset_class_profile(symbol)
        return cls(
            stop_loss_pct=profile.get("stop_loss_pct", STOP_LOSS_PCT),
            trailing_stop_activation=profile.get(
                "trailing_stop_activation", TRAILING_STOP_ACTIVATION
            ),
            trailing_stop_pct=profile.get("trailing_stop_pct", TRAILING_STOP_PCT),
            take_profit_levels=profile.get(
                "take_profit_levels", list(TAKE_PROFIT_LEVELS)
            ),
            max_position_pct=profile.get("max_position_pct", MAX_POSITION_PCT),
        )
