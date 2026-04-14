"""Multi-signal consensus strategy."""

import logging
from typing import List

import pandas as pd

from . import Strategy, register_strategy

logger = logging.getLogger(__name__)


@register_strategy
class MultiSignalStrategy(Strategy):
    """Consensus-based: counts 5 sub-signals, needs 2.5+ aligned in trends, 3.0+ in ranges."""

    name = "MultiSignal"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        symbol = portfolio.get("symbol", "?")

        if len(df) < 3:
            logger.info(f"[MultiSignal/{symbol}] Silent — insufficient data ({len(df)} bars)")
            return []
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr.get("close")
        sma_20 = curr.get("sma_20")
        sma_50 = curr.get("sma_50")
        rsi = curr.get("rsi_14")
        macd_hist = curr.get("macd_histogram")
        prev_macd = prev.get("macd_histogram")
        adx = curr.get("adx_14")
        bb_upper = curr.get("bb_upper")
        bb_lower = curr.get("bb_lower")
        volume_ratio = curr.get("volume_ratio")

        required = [price, sma_20, sma_50, rsi, macd_hist, bb_upper, bb_lower]
        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in required):
            missing = [n for n, v in [("price", price), ("sma_20", sma_20), ("sma_50", sma_50),
                       ("rsi", rsi), ("macd_hist", macd_hist), ("bb_upper", bb_upper), ("bb_lower", bb_lower)]
                       if v is None or (isinstance(v, float) and pd.isna(v))]
            logger.info(f"[MultiSignal/{symbol}] Silent — missing data: {missing}")
            return []

        bull = 0
        bear = 0
        reasons = []

        # 1. Price vs SMA trend
        if price > sma_20 > sma_50:
            bull += 1; reasons.append("trend_up")
        elif price < sma_20 < sma_50:
            bear += 1; reasons.append("trend_down")

        # 2. MACD
        if macd_hist > 0:
            bull += 1
            if prev_macd is not None and not pd.isna(prev_macd) and prev_macd < 0:
                bull += 0.5; reasons.append("macd_cross_up")
            else:
                reasons.append("macd_pos")
        elif macd_hist < 0:
            bear += 1
            if prev_macd is not None and not pd.isna(prev_macd) and prev_macd > 0:
                bear += 0.5; reasons.append("macd_cross_down")
            else:
                reasons.append("macd_neg")

        # 3. RSI
        if rsi < 35:
            bull += 1; reasons.append(f"rsi_low={rsi:.0f}")
        elif rsi > 65:
            bear += 1; reasons.append(f"rsi_high={rsi:.0f}")

        # 4. Bollinger
        bb_w = bb_upper - bb_lower
        bb_pos = None
        if bb_w > 0:
            bb_pos = (price - bb_lower) / bb_w
            if bb_pos < 0.2:
                bull += 1; reasons.append(f"bb_low={bb_pos:.2f}")
            elif bb_pos > 0.8:
                bear += 1; reasons.append(f"bb_high={bb_pos:.2f}")

        # 5. Volume
        if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio > 1.3:
            if bull > bear:
                bull += 0.5
            elif bear > bull:
                bear += 0.5
            reasons.append("vol_confirm")

        is_trending = adx is not None and not pd.isna(adx) and adx > 25
        min_signals = 2.5 if is_trending else 3.0
        reason_str = " ".join(reasons)
        if is_trending:
            reason_str += f" ADX={adx:.0f}(trend)"

        signals = []
        if bull >= min_signals and bull > bear:
            signals.append({"action": "BUY", "weight": min(bull / 5.0, 0.8), "reason": reason_str})
        elif bear >= min_signals and bear > bull:
            signals.append({"action": "SELL", "weight": min(bear / 5.0, 0.8), "reason": reason_str})

        if not signals:
            logger.info(
                f"[MultiSignal/{symbol}] Silent — bull={bull:.1f} bear={bear:.1f} "
                f"need>={min_signals:.1f} ({'trend' if is_trending else 'range'}), "
                f"ADX={adx:.1f} RSI={rsi:.0f} "
                f"BB={f'{bb_pos:.2f}' if bb_pos is not None else 'N/A'} "
                f"sub=[{', '.join(reasons) or 'none'}]"
            )

        return signals
