"""MACD crossover baseline strategy."""

from typing import List

import pandas as pd

from . import Strategy, register_strategy


@register_strategy
class MACDCrossStrategy(Strategy):
    """Pure MACD histogram crossover — enters on sign change, exits on reversal."""

    name = "MACDCross"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 3:
            return []
        prev_h = df.iloc[-2].get("macd_histogram")
        curr_h = df.iloc[-1].get("macd_histogram")
        if prev_h is None or curr_h is None or pd.isna(prev_h) or pd.isna(curr_h):
            return []
        if prev_h < 0 and curr_h > 0:
            return [{"action": "BUY", "weight": 0.5, "reason": "MACD bullish cross"}]
        if prev_h > 0 and curr_h < 0:
            return [{"action": "SELL", "weight": 0.5, "reason": "MACD bearish cross"}]
        return []
