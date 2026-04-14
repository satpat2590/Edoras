"""
Regime-Aware Strategy — HMM regime detection routing to sub-strategies.

Uses a Hidden Markov Model (or fallback heuristic) to classify the market
into 3 regimes: bull, bear, sideways. Routes to the appropriate sub-strategy:
  - Bull → TSMOM (momentum)
  - Bear → defensive (hold cash / tight stops)
  - Sideways → BollingerReversion (mean reversion)

Based on Koki et al. (2022) and Agakishiev et al. (2025) for crypto HMM regimes.
"""

import logging

import numpy as np
import pandas as pd

from . import Strategy, register_strategy

logger = logging.getLogger(__name__)

# Try HMM — fall back to heuristic if not installed
try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
    # Suppress noisy convergence warnings — heuristic fallback handles non-convergence
    logging.getLogger("hmmlearn").setLevel(logging.ERROR)
except ImportError:
    HMM_AVAILABLE = False


def _detect_regime_heuristic(df: pd.DataFrame, lookback: int = 60) -> str:
    """Heuristic regime detection using ADX + SMA slope + volatility."""
    if len(df) < lookback:
        return "sideways"

    bar = df.iloc[-1]
    close = df["close"]

    # ADX for trend strength
    adx = bar.get("adx_14", 20) or 20

    # SMA slope (50-day)
    sma50 = df["sma_50"].dropna()
    if len(sma50) >= 10:
        sma_slope = (sma50.iloc[-1] - sma50.iloc[-10]) / sma50.iloc[-10]
    else:
        sma_slope = 0

    # Price vs SMA200
    sma200 = bar.get("sma_200", None)
    price_above_200 = close.iloc[-1] > sma200 if sma200 and not pd.isna(sma200) else None

    # Volatility regime
    returns = close.pct_change().dropna()
    recent_vol = returns.iloc[-20:].std() * np.sqrt(365) if len(returns) >= 20 else 0.5

    # Classification
    if adx > 25 and sma_slope > 0.02 and (price_above_200 is True or price_above_200 is None):
        return "bull"
    elif adx > 25 and sma_slope < -0.02:
        return "bear"
    elif recent_vol > 0.8 and sma_slope < -0.01:
        return "bear"
    else:
        return "sideways"


def _detect_regime_hmm(df: pd.DataFrame, lookback: int = 120) -> str:
    """HMM-based regime detection with 3 states."""
    if not HMM_AVAILABLE or len(df) < lookback:
        return _detect_regime_heuristic(df, lookback)

    close = df["close"].iloc[-lookback:]
    returns = close.pct_change().dropna()
    vol = returns.rolling(20).std().dropna()

    if len(vol) < 40:
        return _detect_regime_heuristic(df, lookback)

    # Features: returns + rolling volatility
    X = np.column_stack([
        returns.iloc[-len(vol):].values,
        vol.values,
    ])

    try:
        model = GaussianHMM(n_components=3, covariance_type="diag",
                            n_iter=50, random_state=42)
        model.fit(X)
        states = model.predict(X)
        current_state = states[-1]

        # Classify states by mean return
        state_means = {}
        for s in range(3):
            mask = states == s
            if mask.sum() > 0:
                state_means[s] = X[mask, 0].mean()

        sorted_states = sorted(state_means.items(), key=lambda x: x[1])
        # Lowest mean return = bear, highest = bull, middle = sideways
        state_map = {
            sorted_states[0][0]: "bear",
            sorted_states[1][0]: "sideways",
            sorted_states[2][0]: "bull",
        }
        return state_map[current_state]

    except Exception:
        return _detect_regime_heuristic(df, lookback)


@register_strategy
class RegimeAwareStrategy(Strategy):
    """Routes to momentum in trends, mean-reversion in ranges, defensive in bears."""

    name = "RegimeAware"

    def __init__(self, use_hmm: bool = True, hmm_available: bool = True):
        self.use_hmm = use_hmm
        self._last_regime = "sideways"

    def get_parameters(self) -> dict:
        return {"use_hmm": self.use_hmm, "hmm_available": HMM_AVAILABLE}

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> list:
        symbol = portfolio.get("symbol", "?")

        if len(df) < 60:
            logger.info(f"[RegimeAware/{symbol}] Silent — insufficient data ({len(df)} bars, need 60)")
            return []

        # Detect regime
        if self.use_hmm and HMM_AVAILABLE:
            regime = _detect_regime_hmm(df)
        else:
            regime = _detect_regime_heuristic(df)
        self._last_regime = regime

        bar = df.iloc[-1]
        close = bar["close"]
        rsi = bar.get("rsi_14", 50) or 50
        adx = bar.get("adx_14", 20) or 20
        macd_h = bar.get("macd_histogram", 0) or 0
        vol_ratio = bar.get("volume_ratio", 1.0) or 1.0

        signals = []

        if regime == "bull":
            signals = self._bull_signals(df, portfolio, bar, close, rsi, adx, macd_h, vol_ratio)
        elif regime == "bear":
            signals = self._bear_signals(df, portfolio, bar, close, rsi, adx)
        else:  # sideways
            signals = self._sideways_signals(df, portfolio, bar, close, rsi, adx, vol_ratio)

        if not signals:
            bb_upper = bar.get("bb_upper")
            bb_lower = bar.get("bb_lower")
            bb_pos = "N/A"
            if bb_upper and bb_lower and not pd.isna(bb_upper) and not pd.isna(bb_lower) and bb_upper != bb_lower:
                bb_pos = f"{(close - bb_lower) / (bb_upper - bb_lower):.2f}"
            pos_qty = portfolio.get("position_qty", 0)
            logger.info(
                f"[RegimeAware/{symbol}] Silent — regime={regime}, "
                f"ADX={adx:.1f} RSI={rsi:.0f} BB_pos={bb_pos} MACD_h={macd_h:.4f} "
                f"pos_qty={pos_qty}"
            )

        return signals

    def _bull_signals(self, df, portfolio, bar, close, rsi, adx, macd_h, vol_ratio):
        """Momentum-following in bull regime."""
        signals = []

        if portfolio["position_qty"] == 0:
            # TSMOM-style: positive 60d return + MACD confirmation
            if len(df) > 63:
                ret_60d = (close / df["close"].iloc[-63]) - 1
            else:
                ret_60d = 0

            if ret_60d > 0.02 and macd_h > 0 and rsi < 72:
                # Inverse-vol sizing
                returns = df["close"].pct_change().dropna()
                recent_vol = returns.iloc[-30:].std() * np.sqrt(365) if len(returns) >= 30 else 0.5
                weight = min(0.15 / max(recent_vol, 0.05), 0.9)

                if vol_ratio > 1.2:
                    weight = min(weight * 1.15, 0.95)

                signals.append({
                    "action": "BUY",
                    "weight": round(weight, 4),
                    "reason": (
                        f"Regime=BULL momentum: 60d_ret={ret_60d:.2%} "
                        f"MACD_h={macd_h:.4f} ADX={adx:.0f} RSI={rsi:.0f}"
                    ),
                })

        elif portfolio["position_qty"] > 0:
            # Exit if momentum fades
            if macd_h < 0 and rsi > 70:
                signals.append({
                    "action": "SELL",
                    "weight": 1.0,
                    "reason": f"Regime=BULL exit: momentum fading MACD_h={macd_h:.4f} RSI={rsi:.0f}",
                })

        return signals

    def _bear_signals(self, df, portfolio, bar, close, rsi, adx):
        """Defensive in bear regime — exit positions, only buy extreme oversold."""
        signals = []

        if portfolio["position_qty"] > 0:
            # Exit unless deeply oversold (potential reversal)
            if rsi > 35:
                signals.append({
                    "action": "SELL",
                    "weight": 1.0,
                    "reason": f"Regime=BEAR defensive exit: RSI={rsi:.0f} ADX={adx:.0f}",
                })
        elif portfolio["position_qty"] == 0:
            # Only buy extreme oversold bounce plays
            if rsi < 22 and adx > 30:
                signals.append({
                    "action": "BUY",
                    "weight": 0.3,  # small position
                    "reason": f"Regime=BEAR oversold bounce: RSI={rsi:.0f} ADX={adx:.0f}",
                })

        return signals

    def _sideways_signals(self, df, portfolio, bar, close, rsi, adx, vol_ratio):
        """Mean-reversion in sideways regime (Bollinger-style)."""
        signals = []

        bb_upper = bar.get("bb_upper", None)
        bb_lower = bar.get("bb_lower", None)

        if bb_upper is None or bb_lower is None or pd.isna(bb_upper) or pd.isna(bb_lower):
            return []
        if bb_upper == bb_lower:
            return []

        bb_pos = (close - bb_lower) / (bb_upper - bb_lower)

        if portfolio["position_qty"] == 0:
            if bb_pos < 0.1 and rsi < 38:
                weight = 0.7
                if vol_ratio > 1.3:
                    weight = 0.8
                signals.append({
                    "action": "BUY",
                    "weight": round(weight, 4),
                    "reason": (
                        f"Regime=SIDEWAYS mean-revert BUY: bb_pos={bb_pos:.2f} "
                        f"RSI={rsi:.0f} ADX={adx:.0f}"
                    ),
                })

        elif portfolio["position_qty"] > 0:
            if bb_pos > 0.9 and rsi > 62:
                signals.append({
                    "action": "SELL",
                    "weight": 1.0,
                    "reason": (
                        f"Regime=SIDEWAYS mean-revert SELL: bb_pos={bb_pos:.2f} "
                        f"RSI={rsi:.0f}"
                    ),
                })

        return signals


@register_strategy
class RegimeAwareHeuristicStrategy(Strategy):
    """Regime-aware routing using heuristic detection only (no HMM dependency)."""

    name = "RegimeAware_Heuristic"

    def __init__(self):
        self._inner = RegimeAwareStrategy(use_hmm=False)

    def get_parameters(self) -> dict:
        return self._inner.get_parameters()

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> list:
        return self._inner.generate_signals(df, portfolio)
