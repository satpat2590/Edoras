"""ADX trend-following strategy."""

from typing import List

import pandas as pd

from . import Strategy, register_strategy


@register_strategy
class ADXTrendStrategy(Strategy):
    """Trend-following using ADX confirmation — enters confirmed trends, exits on exhaustion."""

    name = "ADXTrend"

    def __init__(self, adx_threshold=25, adx_strong=35):
        self.adx_threshold = adx_threshold
        self.adx_strong = adx_strong

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 3:
            return []
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        adx = curr.get("adx_14")
        rsi = curr.get("rsi_14")
        price = curr.get("close")
        sma_20 = curr.get("sma_20")
        sma_50 = curr.get("sma_50")
        macd_hist = curr.get("macd_histogram")
        prev_adx = prev.get("adx_14")

        if any(v is None or (isinstance(v, float) and pd.isna(v))
               for v in [adx, rsi, price, sma_20, sma_50, macd_hist]):
            return []

        signals = []
        if adx > self.adx_threshold and prev_adx is not None and not pd.isna(prev_adx):
            if price > sma_20 > sma_50 and macd_hist > 0 and rsi < 65:
                w = 0.7 if adx > self.adx_strong else 0.5
                signals.append({"action": "BUY", "weight": w,
                                "reason": f"ADX={adx:.1f} trend-up RSI={rsi:.1f}"})
            elif price < sma_20 < sma_50 and macd_hist < 0 and rsi > 35:
                w = 0.7 if adx > self.adx_strong else 0.5
                signals.append({"action": "SELL", "weight": w,
                                "reason": f"ADX={adx:.1f} trend-down RSI={rsi:.1f}"})

        if (prev_adx is not None and not pd.isna(prev_adx) and adx < prev_adx - 5
                and adx > 30 and portfolio.get("position_qty", 0) > 0):
            signals.append({"action": "SELL", "weight": 0.4,
                            "reason": f"ADX declining {prev_adx:.1f}->{adx:.1f}"})
        return signals

    def get_parameters(self) -> dict:
        return {"adx_threshold": self.adx_threshold, "adx_strong": self.adx_strong}
