"""ScoreBased and EnhancedScoreBased strategies."""

import sqlite3
from typing import List

import pandas as pd

from . import Strategy, register_strategy


@register_strategy
class ScoreBasedStrategy(Strategy):
    """RSI oversold/overbought with MACD momentum confirmation."""

    name = "ScoreBased"

    def __init__(self, rsi_oversold=30, rsi_overbought=70, min_strength=30):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.min_strength = min_strength

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 2:
            return []
        latest = df.iloc[-1]
        rsi = latest.get("rsi_14")
        macd_hist = latest.get("macd_histogram")
        if rsi is None or macd_hist is None or pd.isna(rsi) or pd.isna(macd_hist):
            return []

        signals = []
        if rsi < self.rsi_oversold and macd_hist > 0:
            strength = (self.rsi_oversold - rsi) * 3.33 + min(macd_hist * 100, 50)
            if strength >= self.min_strength:
                signals.append({"action": "BUY", "weight": min(strength / 100, 1.0),
                                "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f}"})
        elif rsi > self.rsi_overbought and macd_hist < 0:
            strength = (rsi - self.rsi_overbought) * 3.33 + min(abs(macd_hist) * 100, 50)
            if strength >= self.min_strength:
                signals.append({"action": "SELL", "weight": min(strength / 100, 1.0),
                                "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f}"})
        elif rsi < 35 and macd_hist > 0:
            strength = (35 - rsi) * 2.0 + min(macd_hist * 100, 30)
            if strength >= self.min_strength:
                signals.append({"action": "BUY", "weight": min(strength / 100, 1.0),
                                "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f} (weak)"})
        elif rsi > 65 and macd_hist < 0:
            strength = (rsi - 65) * 2.0 + min(abs(macd_hist) * 100, 30)
            if strength >= self.min_strength:
                signals.append({"action": "SELL", "weight": min(strength / 100, 1.0),
                                "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f} (weak)"})
        return signals

    def get_parameters(self) -> dict:
        return {"rsi_oversold": self.rsi_oversold, "rsi_overbought": self.rsi_overbought,
                "min_strength": self.min_strength}


class ScoreBasedRelaxedStrategy(ScoreBasedStrategy):
    """Wider RSI bands (35/65), lower min strength (20). Generates more signals."""

    name = "ScoreBasedRelaxed"

    def __init__(self):
        super().__init__(rsi_oversold=35, rsi_overbought=65, min_strength=20)


# Register relaxed variant
register_strategy(ScoreBasedRelaxedStrategy)


@register_strategy
class EnhancedScoreBasedStrategy(ScoreBasedStrategy):
    """ScoreBased with ADX, volume, multi-TF alignment, and VIX regime multipliers."""

    name = "EnhancedScoreBased"

    def __init__(self, db_path: str = None, rsi_oversold=30, rsi_overbought=70, min_strength=30):
        super().__init__(rsi_oversold=rsi_oversold, rsi_overbought=rsi_overbought,
                         min_strength=min_strength)
        self.db_path = db_path

    def _get_adx_multiplier(self, adx, action, macd_hist, rsi):
        if adx is None:
            return 1.0
        if adx > 25:
            if (action == "BUY" and macd_hist > 0) or (action == "SELL" and macd_hist < 0):
                return 1.3
            return 0.7
        if (action == "BUY" and rsi < 35) or (action == "SELL" and rsi > 65):
            return 1.2
        return 1.0

    def _get_volume_multiplier(self, volume_ratio):
        if volume_ratio is not None and volume_ratio > 1.2:
            return 1.2
        return 1.0

    def _get_alignment_multiplier(self, symbol, timestamp):
        if not self.db_path:
            return 1.0
        conn = sqlite3.connect(self.db_path)
        try:
            q = ("SELECT i.rsi_14, i.macd_histogram, i.sma_20, i.sma_50, c.close "
                 "FROM indicators i JOIN candlesticks c ON i.symbol=c.symbol "
                 "AND i.timeframe=c.timeframe AND i.timestamp=c.timestamp "
                 "WHERE i.symbol=? AND i.timeframe=? AND i.timestamp<=? "
                 "ORDER BY i.timestamp DESC LIMIT 1")
            cur = conn.cursor()
            cur.execute(q, (symbol, "1h", timestamp))
            r1 = cur.fetchone()
            cur.execute(q, (symbol, "4h", timestamp))
            r4 = cur.fetchone()
            if not r1 or not r4:
                return 1.0
            score = 0.0
            if r1[4] and r1[2] and r4[4] and r4[2]:
                if (r1[4] > r1[2]) == (r4[4] > r4[2]):
                    score += 0.25
            if r1[2] and r1[3] and r4[2] and r4[3]:
                if (r1[2] > r1[3]) == (r4[2] > r4[3]):
                    score += 0.25
            if r1[1] is not None and r4[1] is not None:
                if (r1[1] > 0) == (r4[1] > 0):
                    score += 0.25
            z = lambda r: "os" if r and r < 30 else ("ob" if r and r > 70 else "n")
            if z(r1[0]) == z(r4[0]):
                score += 0.25
            if score >= 0.75:
                return 1.3
            if score >= 0.5:
                return 1.1
            if score < 0.25:
                return 0.5
            return 0.8
        finally:
            conn.close()

    def _get_vix_multiplier(self, timestamp, action):
        if not self.db_path:
            return 1.0
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT close FROM candlesticks WHERE symbol='^VIX' AND timeframe='1d' "
                        "AND timestamp<=? ORDER BY timestamp DESC LIMIT 1", (timestamp,))
            row = cur.fetchone()
            if not row:
                return 1.0
            vix = row[0]
            if vix < 20:
                return 1.2 if action == "BUY" else 0.8
            if vix > 30:
                return 0.5 if action == "BUY" else 1.3
            return 1.0
        finally:
            conn.close()

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        raw = super().generate_signals(df, portfolio)
        if not raw:
            return []
        latest = df.iloc[-1]
        ts = int(latest["timestamp"])
        symbol = portfolio.get("symbol", "BTC-USD")
        adx = latest.get("adx_14")
        vol_r = latest.get("volume_ratio")
        macd_h = latest.get("macd_histogram")
        rsi = latest.get("rsi_14")

        enhanced = []
        for sig in raw:
            strength = sig["weight"] * 100
            a = sig["action"]
            strength *= self._get_adx_multiplier(adx, a, macd_h, rsi)
            strength *= self._get_volume_multiplier(vol_r)
            strength *= self._get_alignment_multiplier(symbol, ts)
            strength *= self._get_vix_multiplier(ts, a)
            strength = min(strength, 100)
            if strength >= self.min_strength:
                sig["weight"] = min(strength / 100, 1.0)
                enhanced.append(sig)
        return enhanced
