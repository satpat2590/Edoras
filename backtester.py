#!/usr/bin/env python3
"""
Backtesting engine for crypto and equity strategies.
Supports walk-forward validation and detailed performance reporting.
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH, PAPER_INITIAL_CAPITAL, PAPER_TRANSACTION_COST,
    STOP_LOSS_PCT, TRAILING_STOP_ACTIVATION, TRAILING_STOP_PCT,
    TAKE_PROFIT_LEVELS, BACKTEST_RESULTS_DIR,
)
from indicator_calculator import calculate_all_indicators

logger = logging.getLogger(__name__)


# ── Result types ─────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    side: str  # "BUY" or "SELL"
    price: float
    quantity: float
    timestamp: datetime
    reason: str = ""


@dataclass
class BacktestMetrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration_days: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_trades: int = 0
    avg_holding_days: float = 0.0
    calmar_ratio: float = 0.0


@dataclass
class BacktestResult:
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)


# ── Strategy base class ──────────────────────────────────────────────────

class Strategy:
    """Base class for backtesting strategies."""

    name: str = "BaseStrategy"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        """
        Given a DataFrame (with indicators) up to the current bar,
        return a list of signal dicts:
            [{"symbol": ..., "action": "BUY"/"SELL", "weight": 0-1, "reason": "..."}]
        """
        raise NotImplementedError

    def get_parameters(self) -> dict:
        return {}


class ScoreBasedStrategy(Strategy):
    """Replicates the existing AdvancedScoringModel + signal detection logic."""

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
        # Strong buy: oversold + bullish MACD
        if rsi < self.rsi_oversold and macd_hist > 0:
            strength = (self.rsi_oversold - rsi) * 3.33 + min(macd_hist * 100, 50)
            if strength >= self.min_strength:
                signals.append({"action": "BUY", "weight": min(strength / 100, 1.0), "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f}"})
        # Strong sell: overbought + bearish MACD
        elif rsi > self.rsi_overbought and macd_hist < 0:
            strength = (rsi - self.rsi_overbought) * 3.33 + min(abs(macd_hist) * 100, 50)
            if strength >= self.min_strength:
                signals.append({"action": "SELL", "weight": min(strength / 100, 1.0), "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f}"})
        # Weak buy: near oversold + bullish MACD
        elif rsi < 35 and macd_hist > 0:
            strength = (35 - rsi) * 2.0 + min(macd_hist * 100, 30)
            if strength >= self.min_strength:
                signals.append({"action": "BUY", "weight": min(strength / 100, 1.0), "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f} (weak)"})
        # Weak sell: near overbought + bearish MACD
        elif rsi > 65 and macd_hist < 0:
            strength = (rsi - 65) * 2.0 + min(abs(macd_hist) * 100, 30)
            if strength >= self.min_strength:
                signals.append({"action": "SELL", "weight": min(strength / 100, 1.0), "reason": f"RSI={rsi:.1f} MACD_H={macd_hist:.4f} (weak)"})
        return signals

    def get_parameters(self) -> dict:
        return {"rsi_oversold": self.rsi_oversold, "rsi_overbought": self.rsi_overbought, "min_strength": self.min_strength}


class MACDCrossStrategy(Strategy):
    """Simple MACD crossover baseline."""

    name = "MACDCross"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 3:
            return []
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        prev_h = prev.get("macd_histogram")
        curr_h = curr.get("macd_histogram")
        if prev_h is None or curr_h is None or pd.isna(prev_h) or pd.isna(curr_h):
            return []
        # Bullish cross: histogram goes from negative to positive
        if prev_h < 0 and curr_h > 0:
            return [{"action": "BUY", "weight": 0.5, "reason": "MACD bullish cross"}]
        # Bearish cross
        if prev_h > 0 and curr_h < 0:
            return [{"action": "SELL", "weight": 0.5, "reason": "MACD bearish cross"}]
        return []


class EnhancedScoreBasedStrategy(ScoreBasedStrategy):
    """
    ScoreBasedStrategy with enhancement multipliers (ADX, volume, multi‑timeframe alignment, VIX regime).
    Skips sentiment (no historical data).
    """
    
    name = "EnhancedScoreBased"
    
    def __init__(self, db_path: str, rsi_oversold=30, rsi_overbought=70, min_strength=30):
        super().__init__(rsi_oversold=rsi_oversold, rsi_overbought=rsi_overbought, min_strength=min_strength)
        self.db_path = db_path
    
    def _get_adx_multiplier(self, adx: float, action: str, macd_hist: float, rsi: float) -> float:
        """ADX multiplier: trending vs ranging."""
        if adx is None:
            return 1.0
        if adx > 25:  # trending
            # Favor signals aligned with MACD direction
            if (action == "BUY" and macd_hist > 0) or (action == "SELL" and macd_hist < 0):
                return 1.3
            else:
                return 0.7
        else:  # ranging
            # Favor mean‑reversion
            if (action == "BUY" and rsi < 35) or (action == "SELL" and rsi > 65):
                return 1.2
        return 1.0
    
    def _get_volume_multiplier(self, volume_ratio: float) -> float:
        """Volume confirmation multiplier."""
        if volume_ratio is not None and volume_ratio > 1.2:
            return 1.2
        return 1.0
    
    def _get_alignment_multiplier(self, symbol: str, timestamp: int) -> float:
        """
        Compute multi‑timeframe alignment (1h vs 4h) for a given timestamp.
        Returns multiplier 0.5‑1.3.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            # Get 1h indicators at or before timestamp (most recent)
            query_1h = """
            SELECT i.rsi_14 as rsi, i.macd_histogram as macd_hist, i.sma_20, i.sma_50, c.close as price
            FROM indicators i
            JOIN candlesticks c ON i.symbol=c.symbol AND i.timeframe=c.timeframe AND i.timestamp=c.timestamp
            WHERE i.symbol=? AND i.timeframe='1h' AND i.timestamp <= ?
            ORDER BY i.timestamp DESC LIMIT 1
            """
            cur = conn.cursor()
            cur.execute(query_1h, (symbol, timestamp))
            row_1h = cur.fetchone()
            
            # Get 4h indicators at or before timestamp
            query_4h = query_1h.replace("'1h'", "'4h'")
            cur.execute(query_4h, (symbol, timestamp))
            row_4h = cur.fetchone()
            
            if not row_1h or not row_4h:
                return 1.0
            
            # Unpack rows
            rsi_1h, macd_1h, sma20_1h, sma50_1h, price_1h = row_1h
            rsi_4h, macd_4h, sma20_4h, sma50_4h, price_4h = row_4h
            
            score = 0.0
            max_score = 0.0
            
            # 1. Price vs SMA20 alignment
            if price_1h is not None and sma20_1h is not None and price_4h is not None and sma20_4h is not None:
                bullish_1h = price_1h > sma20_1h
                bullish_4h = price_4h > sma20_4h
                if bullish_1h == bullish_4h:
                    score += 0.25
                max_score += 0.25
            
            # 2. SMA20 vs SMA50 alignment
            if sma20_1h is not None and sma50_1h is not None and sma20_4h is not None and sma50_4h is not None:
                trend_up_1h = sma20_1h > sma50_1h
                trend_up_4h = sma20_4h > sma50_4h
                if trend_up_1h == trend_up_4h:
                    score += 0.25
                max_score += 0.25
            
            # 3. MACD histogram sign alignment
            if macd_1h is not None and macd_4h is not None:
                bullish_macd_1h = macd_1h > 0
                bullish_macd_4h = macd_4h > 0
                if bullish_macd_1h == bullish_macd_4h:
                    score += 0.25
                max_score += 0.25
            
            # 4. RSI zone alignment
            def rsi_zone(rsi):
                if rsi is None:
                    return None
                if rsi < 30:
                    return 'oversold'
                elif rsi > 70:
                    return 'overbought'
                else:
                    return 'neutral'
            zone_1h = rsi_zone(rsi_1h)
            zone_4h = rsi_zone(rsi_4h)
            if zone_1h is not None and zone_4h is not None and zone_1h == zone_4h:
                score += 0.25
            max_score += 0.25
            
            if max_score == 0:
                return 1.0
            
            alignment = score / max_score
            # Convert to multiplier
            if alignment >= 0.75:
                return 1.3
            elif alignment >= 0.5:
                return 1.1
            elif alignment < 0.25:
                return 0.5
            elif alignment < 0.5:
                return 0.8
            return 1.0
        finally:
            conn.close()
    
    def _get_vix_regime_multiplier(self, timestamp: int, action: str) -> float:
        """
        Get VIX‑based regime multiplier.
        VIX < 20 → risk‑on, VIX 20‑30 → neutral, VIX > 30 → risk‑off.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            # Get VIX close at or before timestamp (daily)
            query = """
            SELECT close FROM candlesticks 
            WHERE symbol='^VIX' AND timeframe='1d' AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT 1
            """
            cur = conn.cursor()
            cur.execute(query, (timestamp,))
            row = cur.fetchone()
            if not row:
                return 1.0
            vix = row[0]
            
            if vix < 20:
                # risk‑on: amplify buys, dampen sells
                if action == "BUY":
                    return 1.2
                elif action == "SELL":
                    return 0.8
            elif vix > 30:
                # risk‑off: dampen buys, amplify sells
                if action == "BUY":
                    return 0.5
                elif action == "SELL":
                    return 1.3
            return 1.0  # neutral
        finally:
            conn.close()
    
    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        # First get raw signals from parent class
        raw_signals = super().generate_signals(df, portfolio)
        if not raw_signals:
            return []
        
        enhanced_signals = []
        latest = df.iloc[-1]
        timestamp = int(latest["timestamp"])
        symbol = portfolio.get("symbol", "BTC-USD")  # TODO: pass symbol properly
        
        # Get indicators for multipliers
        adx = latest.get("adx_14")
        volume_ratio = latest.get("volume_ratio")
        macd_hist = latest.get("macd_histogram")
        rsi = latest.get("rsi_14")
        
        for sig in raw_signals:
            strength = sig.get("weight", 0.5) * 100  # weight is 0‑1, convert back to 0‑100
            action = sig["action"]
            
            # Apply enhancement multipliers
            strength *= self._get_adx_multiplier(adx, action, macd_hist, rsi)
            strength *= self._get_volume_multiplier(volume_ratio)
            strength *= self._get_alignment_multiplier(symbol, timestamp)
            strength *= self._get_vix_regime_multiplier(timestamp, action)
            
            # Cap strength at 100
            strength = min(strength, 100)
            
            # Only keep if strength >= min_strength (already filtered by parent, but re-check)
            if strength >= self.min_strength:
                # Convert back to weight (0‑1)
                sig["weight"] = min(strength / 100, 1.0)
                enhanced_signals.append(sig)
        
        return enhanced_signals


class ADXTrendStrategy(Strategy):
    """
    Trend-following strategy using ADX for trend strength confirmation.
    Buy when ADX > threshold and price above SMA, sell when trend weakens.
    """

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

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [adx, rsi, price, sma_20, sma_50, macd_hist]):
            return []

        signals = []

        # Trend is strengthening (ADX rising above threshold)
        if adx > self.adx_threshold and prev_adx is not None and not pd.isna(prev_adx):
            # Bullish trend: price above SMAs, MACD positive, ADX confirms
            if price > sma_20 > sma_50 and macd_hist > 0:
                weight = 0.5
                if adx > self.adx_strong:
                    weight = 0.7
                if rsi < 65:  # not overbought
                    signals.append({"action": "BUY", "weight": weight,
                                    "reason": f"ADX={adx:.1f} trend-up SMA20>{sma_50:.0f} RSI={rsi:.1f}"})

            # Bearish trend: price below SMAs, MACD negative
            elif price < sma_20 < sma_50 and macd_hist < 0:
                weight = 0.5
                if adx > self.adx_strong:
                    weight = 0.7
                if rsi > 35:  # not oversold
                    signals.append({"action": "SELL", "weight": weight,
                                    "reason": f"ADX={adx:.1f} trend-down SMA20<{sma_50:.0f} RSI={rsi:.1f}"})

        # Trend exhaustion: ADX was strong but is now declining sharply
        if prev_adx is not None and not pd.isna(prev_adx) and adx < prev_adx - 5 and adx > 30:
            if portfolio.get("position_qty", 0) > 0:
                signals.append({"action": "SELL", "weight": 0.4,
                                "reason": f"ADX declining {prev_adx:.1f}→{adx:.1f} trend exhaustion"})

        return signals


class BollingerReversionStrategy(Strategy):
    """
    Mean-reversion strategy using Bollinger Bands.
    Buy at lower band (oversold), sell at upper band (overbought).
    Uses ADX < 25 as ranging confirmation.
    """

    name = "BollingerReversion"

    def __init__(self, bb_threshold=0.05, adx_range_max=25):
        self.bb_threshold = bb_threshold  # how close to band (as % of band width)
        self.adx_range_max = adx_range_max

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 3:
            return []
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr.get("close")
        bb_upper = curr.get("bb_upper")
        bb_lower = curr.get("bb_lower")
        bb_middle = curr.get("bb_middle")
        rsi = curr.get("rsi_14")
        adx = curr.get("adx_14")
        volume_ratio = curr.get("volume_ratio")

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [price, bb_upper, bb_lower, bb_middle, rsi]):
            return []

        signals = []
        bb_width = bb_upper - bb_lower
        if bb_width <= 0:
            return []

        # Position within bands (0 = lower, 1 = upper)
        bb_position = (price - bb_lower) / bb_width

        # Only trade in ranging markets (ADX < threshold or ADX not available)
        is_ranging = adx is None or pd.isna(adx) or adx < self.adx_range_max

        if is_ranging:
            # Buy: price near or below lower band + RSI confirms oversold
            if bb_position < 0.1 and rsi < 40:
                weight = 0.5 + (0.3 * (1 - bb_position))  # stronger when further below band
                if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio > 1.2:
                    weight = min(weight + 0.1, 1.0)  # volume confirmation
                signals.append({"action": "BUY", "weight": min(weight, 1.0),
                                "reason": f"BB_pos={bb_position:.2f} RSI={rsi:.1f} ADX={adx if adx and not pd.isna(adx) else 0:.1f}"})

            # Sell: price near or above upper band + RSI confirms overbought
            elif bb_position > 0.9 and rsi > 60:
                weight = 0.5 + (0.3 * bb_position)
                signals.append({"action": "SELL", "weight": min(weight, 1.0),
                                "reason": f"BB_pos={bb_position:.2f} RSI={rsi:.1f} ADX={adx if adx and not pd.isna(adx) else 0:.1f}"})

        return signals


class MultiSignalStrategy(Strategy):
    """
    Combined strategy that uses multiple confirmations.
    Requires ≥3 of 5 signals to agree before taking a trade.
    Adapts to regime: trend-following when ADX > 25, mean-reversion when ADX < 20.
    """

    name = "MultiSignal"

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> List[dict]:
        if len(df) < 3:
            return []
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        price = curr.get("close")
        sma_20 = curr.get("sma_20")
        sma_50 = curr.get("sma_50")
        rsi = curr.get("rsi_14")
        macd_hist = curr.get("macd_histogram")
        prev_macd_hist = prev.get("macd_histogram")
        adx = curr.get("adx_14")
        bb_upper = curr.get("bb_upper")
        bb_lower = curr.get("bb_lower")
        bb_middle = curr.get("bb_middle")
        volume_ratio = curr.get("volume_ratio")

        required = [price, sma_20, sma_50, rsi, macd_hist, bb_upper, bb_lower]
        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in required):
            return []

        # Count bullish/bearish signals
        bull_signals = 0
        bear_signals = 0
        reasons = []

        # 1. Price vs SMA trend
        if price > sma_20 > sma_50:
            bull_signals += 1
            reasons.append("trend_up")
        elif price < sma_20 < sma_50:
            bear_signals += 1
            reasons.append("trend_down")

        # 2. MACD direction
        if macd_hist > 0:
            bull_signals += 1
            if prev_macd_hist is not None and not pd.isna(prev_macd_hist) and prev_macd_hist < 0:
                bull_signals += 0.5  # bonus for fresh cross
                reasons.append("macd_cross_up")
            else:
                reasons.append("macd_pos")
        elif macd_hist < 0:
            bear_signals += 1
            if prev_macd_hist is not None and not pd.isna(prev_macd_hist) and prev_macd_hist > 0:
                bear_signals += 0.5
                reasons.append("macd_cross_down")
            else:
                reasons.append("macd_neg")

        # 3. RSI
        if rsi < 35:
            bull_signals += 1
            reasons.append(f"rsi_low={rsi:.0f}")
        elif rsi > 65:
            bear_signals += 1
            reasons.append(f"rsi_high={rsi:.0f}")

        # 4. Bollinger Band position
        bb_width = bb_upper - bb_lower
        if bb_width > 0:
            bb_pos = (price - bb_lower) / bb_width
            if bb_pos < 0.2:
                bull_signals += 1
                reasons.append(f"bb_low={bb_pos:.2f}")
            elif bb_pos > 0.8:
                bear_signals += 1
                reasons.append(f"bb_high={bb_pos:.2f}")

        # 5. Volume confirmation
        if volume_ratio is not None and not pd.isna(volume_ratio) and volume_ratio > 1.3:
            # Volume confirms whichever direction has more signals
            if bull_signals > bear_signals:
                bull_signals += 0.5
                reasons.append("vol_confirm")
            elif bear_signals > bull_signals:
                bear_signals += 0.5
                reasons.append("vol_confirm")

        # Regime adaptation
        is_trending = adx is not None and not pd.isna(adx) and adx > 25
        is_ranging = adx is not None and not pd.isna(adx) and adx < 20
        min_signals = 2.5 if is_trending else 3.0  # lower bar when trend is confirmed

        signals = []
        reason_str = " ".join(reasons)
        if is_trending:
            reason_str += f" ADX={adx:.0f}(trend)"
        elif is_ranging:
            reason_str += f" ADX={adx:.0f}(range)"

        if bull_signals >= min_signals and bull_signals > bear_signals:
            weight = min(bull_signals / 5.0, 0.8)
            signals.append({"action": "BUY", "weight": weight, "reason": reason_str})
        elif bear_signals >= min_signals and bear_signals > bull_signals:
            weight = min(bear_signals / 5.0, 0.8)
            signals.append({"action": "SELL", "weight": weight, "reason": reason_str})

        return signals


# ── Backtester ───────────────────────────────────────────────────────────

class Backtester:
    """
    Event-driven backtester that replays historical data bar-by-bar.
    Supports stop-loss, trailing stop, and take-profit exits.
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        initial_capital: float = PAPER_INITIAL_CAPITAL,
        transaction_cost: float = PAPER_TRANSACTION_COST,
    ):
        self.db_path = db_path
        self.initial_capital = initial_capital
        self.transaction_cost = transaction_cost

    def _load_data(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Load OHLCV data from DB and compute indicators."""
        conn = sqlite3.connect(self.db_path)
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())

        # Load extra lookback for indicator warmup
        warmup_ts = start_ts - (250 * 86400)

        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candlesticks WHERE symbol=? AND timeframe=? AND timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp",
            conn,
            params=(symbol, timeframe, warmup_ts, end_ts),
        )
        conn.close()

        if df.empty:
            return df

        df["date"] = pd.to_datetime(df["timestamp"], unit="s")
        df = calculate_all_indicators(df)
        # Trim warmup
        df = df[df["timestamp"] >= start_ts].reset_index(drop=True)
        return df

    def run_backtest(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str = "1d",
        start_date: str = "2025-04-01",
        end_date: str = "2026-03-01",
        use_risk_management: bool = True,
    ) -> BacktestResult:
        """Run a full backtest for a single symbol."""
        df = self._load_data(symbol, timeframe, start_date, end_date)
        if df.empty or len(df) < 30:
            logger.warning(f"Insufficient data for {symbol}/{timeframe}: {len(df)} rows")
            return BacktestResult(strategy.name, start_date, end_date, self.initial_capital, self.initial_capital)

        capital = self.initial_capital
        position_qty = 0.0
        entry_price = 0.0
        high_watermark = 0.0
        partial_exits_hit: List[int] = []
        trades: List[Trade] = []
        equity_values = []
        equity_dates = []

        for i in range(30, len(df)):
            bar = df.iloc[i]
            price = bar["close"]
            ts = datetime.utcfromtimestamp(int(bar["timestamp"]))
            portfolio_value = capital + position_qty * price

            equity_values.append(portfolio_value)
            equity_dates.append(ts)

            # ── Risk management exits ────────────────────────────────
            if use_risk_management and position_qty > 0:
                # Stop loss
                if price <= entry_price * (1 - STOP_LOSS_PCT):
                    proceeds = position_qty * price * (1 - self.transaction_cost)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", price, position_qty, ts, "stop_loss"))
                    position_qty = 0.0
                    entry_price = 0.0
                    continue

                # Trailing stop
                gain = (price - entry_price) / entry_price
                if gain >= TRAILING_STOP_ACTIVATION:
                    atr = bar.get("atr_14")
                    if atr and not pd.isna(atr) and atr > 0:
                        trail = high_watermark - 2 * atr
                    else:
                        trail = high_watermark * (1 - TRAILING_STOP_PCT)
                    trail = max(trail, entry_price * 1.001)  # breakeven floor
                    if price <= trail:
                        proceeds = position_qty * price * (1 - self.transaction_cost)
                        capital += proceeds
                        trades.append(Trade(symbol, "SELL", price, position_qty, ts, "trailing_stop"))
                        position_qty = 0.0
                        entry_price = 0.0
                        continue

                # Take profit scale-out
                for j, (threshold, sell_pct) in enumerate(TAKE_PROFIT_LEVELS):
                    if j in partial_exits_hit:
                        continue
                    if gain >= threshold:
                        sell_qty = position_qty * sell_pct
                        proceeds = sell_qty * price * (1 - self.transaction_cost)
                        capital += proceeds
                        position_qty -= sell_qty
                        partial_exits_hit.append(j)
                        trades.append(Trade(symbol, "SELL", price, sell_qty, ts, f"take_profit_L{j+1}"))
                        break

                if price > high_watermark:
                    high_watermark = price

            # ── Strategy signals ─────────────────────────────────────
            window = df.iloc[:i + 1]
            portfolio = {"capital": capital, "position_qty": position_qty, "entry_price": entry_price, "symbol": symbol}
            signals = strategy.generate_signals(window, portfolio)

            for sig in signals:
                if sig["action"] == "BUY" and position_qty == 0 and capital > 10:
                    buy_amount = capital * sig["weight"]
                    buy_amount = min(buy_amount, capital * 0.95)  # keep 5% cash reserve
                    cost = buy_amount * (1 + self.transaction_cost)
                    if cost <= capital:
                        qty = buy_amount / price
                        capital -= cost
                        position_qty += qty
                        entry_price = price
                        high_watermark = price
                        partial_exits_hit = []
                        trades.append(Trade(symbol, "BUY", price, qty, ts, sig["reason"]))

                elif sig["action"] == "SELL" and position_qty > 0:
                    proceeds = position_qty * price * (1 - self.transaction_cost)
                    capital += proceeds
                    trades.append(Trade(symbol, "SELL", price, position_qty, ts, sig["reason"]))
                    position_qty = 0.0
                    entry_price = 0.0

        # Close any remaining position at last price
        if position_qty > 0:
            last_price = df.iloc[-1]["close"]
            proceeds = position_qty * last_price * (1 - self.transaction_cost)
            capital += proceeds
            trades.append(Trade(symbol, "SELL", last_price, position_qty,
                                datetime.utcfromtimestamp(int(df.iloc[-1]["timestamp"])), "end_of_backtest"))
            position_qty = 0.0

        final_value = capital
        equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates))

        metrics = self._calculate_metrics(equity_curve, trades, self.initial_capital)

        return BacktestResult(
            strategy_name=strategy.name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_value=final_value,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
        )

    def _calculate_metrics(self, equity_curve: pd.Series, trades: List[Trade], initial: float) -> BacktestMetrics:
        """Calculate performance metrics from equity curve and trades."""
        m = BacktestMetrics()
        if equity_curve.empty:
            return m

        final = equity_curve.iloc[-1]
        m.total_return = (final - initial) / initial
        m.total_trades = len([t for t in trades if t.side == "BUY"])

        # Daily returns
        daily = equity_curve.resample("D").last().dropna()
        returns = daily.pct_change().dropna()

        if len(returns) > 1:
            days = (equity_curve.index[-1] - equity_curve.index[0]).days
            years = max(days / 365.25, 0.01)
            m.annualized_return = (final / initial) ** (1 / years) - 1

            mean_r = returns.mean()
            std_r = returns.std()
            if std_r > 0:
                m.sharpe_ratio = mean_r / std_r * np.sqrt(365)

            downside = returns[returns < 0]
            if len(downside) > 0 and downside.std() > 0:
                m.sortino_ratio = mean_r / downside.std() * np.sqrt(365)

            # Drawdown
            cum = (1 + returns).cumprod()
            running_max = cum.expanding().max()
            dd = (cum - running_max) / running_max
            m.max_drawdown = dd.min()

            # Drawdown duration
            in_dd = dd < 0
            if in_dd.any():
                dd_groups = (~in_dd).cumsum()
                dd_lengths = in_dd.groupby(dd_groups).sum()
                m.max_drawdown_duration_days = dd_lengths.max()

            if m.max_drawdown != 0:
                m.calmar_ratio = m.annualized_return / abs(m.max_drawdown)

        # Trade-level metrics
        buy_trades = [t for t in trades if t.side == "BUY"]
        sell_trades = [t for t in trades if t.side == "SELL"]
        if buy_trades and sell_trades:
            # Pair buys with sells
            wins = []
            losses = []
            holding_days = []
            for i, bt in enumerate(buy_trades):
                # Find corresponding sell
                sells_after = [s for s in sell_trades if s.timestamp > bt.timestamp]
                if sells_after:
                    st = sells_after[0]
                    pnl = (st.price - bt.price) / bt.price
                    if pnl > 0:
                        wins.append(pnl)
                    else:
                        losses.append(pnl)
                    holding_days.append((st.timestamp - bt.timestamp).total_seconds() / 86400)

            total = len(wins) + len(losses)
            if total > 0:
                m.win_rate = len(wins) / total
                m.avg_win = np.mean(wins) if wins else 0
                m.avg_loss = np.mean(losses) if losses else 0
                gross_profit = sum(wins) if wins else 0
                gross_loss = abs(sum(losses)) if losses else 0.001
                m.profit_factor = gross_profit / gross_loss
            if holding_days:
                m.avg_holding_days = np.mean(holding_days)

        return m

    # ── Walk-forward validation ──────────────────────────────────────────

    def walk_forward(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str = "1d",
        start_date: str = "2025-04-01",
        end_date: str = "2026-03-01",
        n_splits: int = 4,
        train_pct: float = 0.7,
    ) -> List[BacktestResult]:
        """Split data into n_splits windows, backtest each out-of-sample."""
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        total_days = (end_dt - start_dt).days
        window_days = total_days // n_splits

        results = []
        for i in range(n_splits):
            w_start = start_dt + timedelta(days=i * window_days)
            w_end = w_start + timedelta(days=window_days)
            train_end = w_start + timedelta(days=int(window_days * train_pct))

            # Test on the out-of-sample portion
            oos_start = train_end.strftime("%Y-%m-%d")
            oos_end = min(w_end, end_dt).strftime("%Y-%m-%d")

            result = self.run_backtest(strategy, symbol, timeframe, oos_start, oos_end)
            result.strategy_name = f"{strategy.name}_split{i+1}"
            results.append(result)

        return results


def format_backtest_report(result: BacktestResult) -> str:
    """Format a single backtest result as readable text."""
    m = result.metrics
    lines = []
    lines.append(f"📊 **Backtest: {result.strategy_name}**")
    lines.append(f"Period: {result.start_date} → {result.end_date}")
    lines.append(f"Capital: ${result.initial_capital:.2f} → ${result.final_value:.2f}")
    lines.append("")
    lines.append("**Performance:**")
    lines.append(f"• Total return: {m.total_return:.2%}")
    lines.append(f"• Annualized: {m.annualized_return:.2%}")
    lines.append(f"• Sharpe: {m.sharpe_ratio:.2f}")
    lines.append(f"• Sortino: {m.sortino_ratio:.2f}")
    lines.append(f"• Max drawdown: {m.max_drawdown:.2%}")
    lines.append(f"• Calmar: {m.calmar_ratio:.2f}")
    lines.append("")
    lines.append("**Trades:**")
    lines.append(f"• Total: {m.total_trades}")
    lines.append(f"• Win rate: {m.win_rate:.1%}")
    lines.append(f"• Profit factor: {m.profit_factor:.2f}")
    lines.append(f"• Avg win: {m.avg_win:.2%} | Avg loss: {m.avg_loss:.2%}")
    lines.append(f"• Avg hold: {m.avg_holding_days:.1f} days")
    return "\n".join(lines)


def main():
    """Run a demo backtest."""
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Backtester")
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-03-01")
    parser.add_argument("--strategy", default="score",
                        choices=["score", "score-relaxed", "macd", "enhanced", "adx-trend", "bollinger", "multi"])
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--no-risk", action="store_true", help="Disable risk management")
    args = parser.parse_args()

    bt = Backtester()
    strat_map = {
        "score": ScoreBasedStrategy(),
        "score-relaxed": ScoreBasedStrategy(rsi_oversold=35, rsi_overbought=65, min_strength=20),
        "macd": MACDCrossStrategy(),
        "enhanced": EnhancedScoreBasedStrategy(db_path=DB_PATH),
        "adx-trend": ADXTrendStrategy(),
        "bollinger": BollingerReversionStrategy(),
        "multi": MultiSignalStrategy(),
    }
    strat_map["score-relaxed"].name = "ScoreBasedRelaxed"
    strategy = strat_map[args.strategy]

    if args.walk_forward:
        results = bt.walk_forward(strategy, args.symbol, args.timeframe, args.start, args.end)
        for r in results:
            print(format_backtest_report(r))
            print()
    else:
        result = bt.run_backtest(strategy, args.symbol, args.timeframe, args.start, args.end,
                                  use_risk_management=not args.no_risk)
        print(format_backtest_report(result))

        # Save results
        os.makedirs(BACKTEST_RESULTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(BACKTEST_RESULTS_DIR, f"{args.strategy}_{args.symbol}_{ts}.txt")
        with open(path, "w") as f:
            f.write(format_backtest_report(result))
        print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
