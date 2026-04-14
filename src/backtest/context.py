"""
StrategyContext — rich context for strategies that need multi-timeframe
or reference data (e.g. VIX).

Strategies opt in by declaring required_timeframes and/or required_references
on the class. The engine pre-loads this data and passes it via
generate_signals_ctx() instead of generate_signals().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import pandas as pd


@dataclass
class StrategyContext:
    """Rich context passed to strategies that opt in via generate_signals_ctx()."""

    # Primary data (same as what generate_signals() receives)
    primary_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    symbol: str = ""
    timeframe: str = ""
    portfolio: dict = field(default_factory=dict)  # legacy portfolio dict

    # Multi-timeframe data, populated if strategy declares required_timeframes
    # e.g. {"1h": df_1h_up_to_current_bar, "4h": df_4h_up_to_current_bar}
    timeframe_data: Dict[str, pd.DataFrame] = field(default_factory=dict)

    # Reference symbol data, populated if strategy declares required_references
    # e.g. {"^VIX": df_vix_up_to_current_bar}
    reference_data: Dict[str, pd.DataFrame] = field(default_factory=dict)

    # Current bar timestamp (for strategies that need precise time alignment)
    current_timestamp: int = 0

    @property
    def has_extra_data(self) -> bool:
        """True if any multi-timeframe or reference data is available."""
        return bool(self.timeframe_data) or bool(self.reference_data)
