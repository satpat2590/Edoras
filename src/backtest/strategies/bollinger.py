"""Bollinger Band mean-reversion strategy."""

import logging
from typing import List

import pandas as pd

from . import Strategy, register_strategy

logger = logging.getLogger(__name__)


@register_strategy
class BollingerReversionStrategy(Strategy):
    """Mean-reversion off Bollinger Bands in ranging markets (ADX < 25)."""

    name = "BollingerReversion"

    def __init__(self, bb_threshold=0.05, adx_range_max=25):
        self.bb_threshold = bb_threshold
        self.adx_range_max = adx_range_max

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        symbol = portfolio.get("symbol", "?")

        if len(df) < 3:
            logger.info(f"[BollingerReversion/{symbol}] Silent — insufficient data ({len(df)} bars)")
            return []
        curr = df.iloc[-1]
        price = curr.get("close")
        bb_upper = curr.get("bb_upper")
        bb_lower = curr.get("bb_lower")
        rsi = curr.get("rsi_14")
        adx = curr.get("adx_14")
        volume_ratio = curr.get("volume_ratio")

        if any(v is None or (isinstance(v, float) and pd.isna(v))
               for v in [price, bb_upper, bb_lower, rsi]):
            missing = [n for n, v in [("price", price), ("bb_upper", bb_upper),
                       ("bb_lower", bb_lower), ("rsi", rsi)]
                       if v is None or (isinstance(v, float) and pd.isna(v))]
            logger.info(f"[BollingerReversion/{symbol}] Silent — missing data: {missing}")
            return []

        bb_width = bb_upper - bb_lower
        if bb_width <= 0:
            logger.info(f"[BollingerReversion/{symbol}] Silent — BB width <= 0")
            return []
        bb_position = (price - bb_lower) / bb_width
        is_ranging = adx is None or pd.isna(adx) or adx < self.adx_range_max

        signals = []
        if is_ranging:
            if bb_position < 0.1 and rsi < 40:
                w = 0.5 + (0.3 * (1 - bb_position))
                if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio > 1.2:
                    w = min(w + 0.1, 1.0)
                signals.append({"action": "BUY", "weight": min(w, 1.0),
                                "reason": f"BB={bb_position:.2f} RSI={rsi:.1f} ADX={adx or 0:.1f}"})
            elif bb_position > 0.9 and rsi > 60:
                w = 0.5 + (0.3 * bb_position)
                signals.append({"action": "SELL", "weight": min(w, 1.0),
                                "reason": f"BB={bb_position:.2f} RSI={rsi:.1f} ADX={adx or 0:.1f}"})

        if not signals:
            blockers = []
            if not is_ranging:
                blockers.append(f"ADX={adx:.1f}>={self.adx_range_max}(trending)")
            if 0.1 <= bb_position <= 0.9:
                blockers.append(f"BB_pos={bb_position:.2f}(neutral,need<0.1|>0.9)")
            if is_ranging and bb_position < 0.1 and rsi >= 40:
                blockers.append(f"RSI={rsi:.1f}>=40")
            if is_ranging and bb_position > 0.9 and rsi <= 60:
                blockers.append(f"RSI={rsi:.1f}<=60")
            logger.info(f"[BollingerReversion/{symbol}] Silent — {', '.join(blockers) or 'no extreme'}")

        return signals

    def get_parameters(self) -> dict:
        return {"bb_threshold": self.bb_threshold, "adx_range_max": self.adx_range_max}
