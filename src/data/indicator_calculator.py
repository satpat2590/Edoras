#!/usr/bin/env python3
"""
Shared indicator calculation module.
Computes all technical indicators from an OHLCV DataFrame.
Used by crypto_data_collector, intraday_update, backtester, and equity_data_collector.
"""

import numpy as np
import pandas as pd
from typing import Optional


def calculate_all_indicators(df: pd.DataFrame, min_periods: int = 20) -> pd.DataFrame:
    """
    Calculate all technical indicators on an OHLCV DataFrame.

    Expected columns: open, high, low, close, volume (and optionally timestamp).
    Returns the same DataFrame with indicator columns added.
    Rows with insufficient data for a given indicator will have NaN.
    """
    df = df.copy()

    # ── Moving averages ──────────────────────────────────────────────────
    df["sma_20"] = df["close"].rolling(window=20).mean()
    df["sma_50"] = df["close"].rolling(window=50).mean()
    df["sma_200"] = df["close"].rolling(window=200).mean()
    df["ema_12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["ema_26"] = df["close"].ewm(span=26, adjust=False).mean()

    # ── RSI (14) ─────────────────────────────────────────────────────────
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # ── MACD (12, 26, 9) ────────────────────────────────────────────────
    df["macd_line"] = df["ema_12"] - df["ema_26"]
    df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
    df["macd_histogram"] = df["macd_line"] - df["macd_signal"]

    # ── Bollinger Bands (20, 2σ) ─────────────────────────────────────────
    df["bb_middle"] = df["sma_20"]  # same as SMA-20
    bb_std = df["close"].rolling(window=20).std()
    df["bb_upper"] = df["bb_middle"] + bb_std * 2
    df["bb_lower"] = df["bb_middle"] - bb_std * 2
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]

    # ── True Range & ATR (14) ────────────────────────────────────────────
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = true_range.rolling(window=14).mean()

    # ── Volume indicators ────────────────────────────────────────────────
    df["volume_sma_20"] = df["volume"].rolling(window=20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma_20"]

    # ── ADX (14) — Wilder's directional movement ────────────────────────
    plus_dm_raw = df["high"].diff()
    minus_dm_raw = -df["low"].diff()
    # Mutual exclusivity: only keep the larger; zero out the other
    plus_dm = pd.Series(np.where(
        (plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), plus_dm_raw, 0.0
    ), index=df.index)
    minus_dm = pd.Series(np.where(
        (minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), minus_dm_raw, 0.0
    ), index=df.index)
    # Wilder's smoothing (EMA with alpha=1/14)
    atr_smooth = true_range.ewm(alpha=1/14, adjust=False).mean()
    # Guard against division by near-zero ATR (microcap coins with tiny prices)
    atr_safe = atr_smooth.replace(0, np.nan).clip(lower=1e-12)
    plus_di = (100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_safe).clip(0)
    minus_di = (100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_safe).clip(0)
    di_sum = plus_di + minus_di
    # DX = 100 * |+DI - -DI| / (+DI + -DI); guard denominator and clip [0, 100]
    dx = (100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)).clip(0, 100)
    df["adx_14"] = dx.ewm(alpha=1/14, adjust=False).mean().clip(0, 100)

    # ── Daily returns (useful downstream) ────────────────────────────────
    df["returns"] = df["close"].pct_change()

    # ── Cleanup helper columns ───────────────────────────────────────────
    df.drop(columns=["plus_dm", "minus_dm"], inplace=True, errors="ignore")

    return df


# Canonical list of indicator column names (for DB inserts)
INDICATOR_COLUMNS = [
    "sma_20", "sma_50", "sma_200",
    "ema_12", "ema_26",
    "rsi_14",
    "macd_line", "macd_signal", "macd_histogram",
    "bb_upper", "bb_middle", "bb_lower", "bb_width",
    "atr_14",
    "volume_sma_20", "volume_ratio",
    "adx_14",
]


# ═══════════════════════════════════════════════════════════════════════════
# Binary prediction market indicators
#
# Standard indicators (RSI, MACD, Bollinger, ADX) are designed for unbounded
# price series. Prediction markets trade in [0, 1] — a probability.
# These indicators capture:
#   - Probability momentum (direction + velocity of price moves)
#   - Mean-reversion signals (prices that deviate from recent trend)
#   - Volatility regime (how uncertain is the market?)
#   - Time decay (how close to expiry, and is the market converging?)
# ═══════════════════════════════════════════════════════════════════════════

def calculate_binary_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate indicators suitable for binary prediction markets (0-1 prices).

    Expected columns: timestamp, open, high, low, close, volume.
    Returns the same DataFrame with binary indicator columns added.
    Needs at least ~20 rows for meaningful output.
    """
    df = df.copy()
    close = df["close"]

    # ── Probability EMAs (fast/slow) ──────────────────────────────────────
    # Short-term and medium-term smoothed probability for crossover signals
    df["prob_ema_8"] = close.ewm(span=8, adjust=False).mean()
    df["prob_ema_21"] = close.ewm(span=21, adjust=False).mean()

    # ── Probability momentum (rate of change) ─────────────────────────────
    # How fast is the implied probability moving? (absolute change over N periods)
    df["prob_roc_6"] = close.diff(6)       # 6-period change in probability
    df["prob_roc_12"] = close.diff(12)     # 12-period change

    # ── Probability velocity (smoothed first derivative) ──────────────────
    # EMA of the 1-period changes — measures sustained directional movement
    df["prob_velocity"] = close.diff().ewm(span=8, adjust=False).mean()

    # ── Probability acceleration ──────────────────────────────────────────
    # Is momentum increasing or fading? Second derivative of price.
    velocity = close.diff().ewm(span=8, adjust=False).mean()
    df["prob_acceleration"] = velocity.diff().ewm(span=5, adjust=False).mean()

    # ── Binary volatility (rolling std of price) ──────────────────────────
    # In a 0-1 space, standard deviation directly measures uncertainty.
    # High vol near 0.5 = genuine uncertainty. High vol near 0/1 = unusual.
    df["prob_volatility_14"] = close.rolling(window=14).std()
    df["prob_volatility_6"] = close.rolling(window=6).std()

    # ── Volatility ratio (short/long) ─────────────────────────────────────
    # > 1 means volatility is expanding (market becoming more uncertain)
    df["vol_ratio"] = df["prob_volatility_6"] / df["prob_volatility_14"]

    # ── Distance from 0.5 (certainty measure) ────────────────────────────
    # 0 = maximum uncertainty (50/50), 0.5 = fully resolved (near 0 or 1)
    df["certainty"] = (close - 0.5).abs()

    # ── Bollinger-like bands for probability ──────────────────────────────
    # Centered on EMA-21, using probability volatility as width
    prob_std = close.rolling(window=20).std()
    df["prob_band_upper"] = df["prob_ema_21"] + 2 * prob_std
    df["prob_band_lower"] = df["prob_ema_21"] - 2 * prob_std
    # Clamp to [0, 1] since probability can't exceed bounds
    df["prob_band_upper"] = df["prob_band_upper"].clip(0, 1)
    df["prob_band_lower"] = df["prob_band_lower"].clip(0, 1)

    # Band position: where is current price within the bands? (0=lower, 1=upper)
    band_range = df["prob_band_upper"] - df["prob_band_lower"]
    df["prob_band_position"] = np.where(
        band_range > 0.001,
        (close - df["prob_band_lower"]) / band_range,
        0.5,
    )

    # ── EMA crossover signal ──────────────────────────────────────────────
    # Positive = fast EMA above slow (bullish probability trend)
    df["ema_crossover"] = df["prob_ema_8"] - df["prob_ema_21"]

    # ── Intrabar range (high-low spread) ──────────────────────────────────
    # Measures how much the price moved within each candle — proxy for activity
    df["bar_range"] = df["high"] - df["low"]
    df["bar_range_ema"] = df["bar_range"].ewm(span=14, adjust=False).mean()

    return df


# Canonical list of binary indicator column names (for DB inserts)
BINARY_INDICATOR_COLUMNS = [
    "prob_ema_8", "prob_ema_21",
    "prob_roc_6", "prob_roc_12",
    "prob_velocity", "prob_acceleration",
    "prob_volatility_14", "prob_volatility_6", "vol_ratio",
    "certainty",
    "prob_band_upper", "prob_band_lower", "prob_band_position",
    "ema_crossover",
    "bar_range", "bar_range_ema",
]
