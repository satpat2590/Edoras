#!/usr/bin/env python3
"""
Regime Monitor — Detects market regime changes and triggers strategy swaps.

Runs periodically (via timer or before signal_trading). Detects bull/bear/sideways
regime per symbol, compares against the current strategy's regime fit, and swaps
to the best-catalogued strategy for the detected regime.

Architecture:
  1. Detect regime per symbol (HMM or heuristic)
  2. Check if current routed strategy fits the regime
  3. If mismatch, query catalogue for best strategy in this regime
  4. Swap via deployer.swap_strategy()
  5. Log all decisions for audit

Based on Koki et al. (2022) HMM regime detection for crypto markets.
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from hmmlearn.hmm import GaussianHMM

    HMM_AVAILABLE = True
    # Suppress noisy convergence warnings — heuristic fallback handles non-convergence
    logging.getLogger("hmmlearn").setLevel(logging.ERROR)
except ImportError:
    HMM_AVAILABLE = False

# Module-level cache for fitted HMM models per symbol
_hmm_cache: Dict[
    str, dict
] = {}  # symbol -> {"model": GaussianHMM, "fitted_at": datetime, "n_samples": int}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH, get_active_portfolios
from data.indicator_calculator import calculate_all_indicators

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Strategy → regime affinity map.
# Each strategy has a primary regime where it works best and regimes where it's acceptable.
STRATEGY_REGIME_FIT = {
    # Momentum strategies → bull
    "ScoreBased": {"best": "bull", "ok": ["sideways"]},  # retired from registry
    "ScoreBasedRelaxed": {"best": "bull", "ok": ["sideways"]},  # retired from registry
    "EnhancedScoreBased": {"best": "bull", "ok": ["sideways"]},
    "MACDCross": {"best": "bull", "ok": []},  # retired from registry
    "TSMOM": {"best": "bull", "ok": []},
    "TSMOM_3M": {"best": "bull", "ok": ["sideways"]},
    # Trend-following → bull
    "ADXTrend": {"best": "bull", "ok": []},
    # Mean-reversion → sideways
    "BollingerReversion": {"best": "sideways", "ok": ["bear"]},
    "PairsTrading": {"best": "sideways", "ok": ["bear"]},
    "PairsTrading_Aggressive": {"best": "sideways", "ok": []},  # retired from registry
    # Bear-specific defensive strategy
    "BearDefensive": {"best": "bear", "ok": ["sideways"]},
    # Multi-factor → sideways (versatile; default for unrouted symbols)
    "MultiSignal": {"best": "sideways", "ok": ["bull", "bear"]},
    # Adaptive → all regimes (they self-route internally)
    "RegimeAware": {"best": "all", "ok": ["bull", "bear", "sideways"]},
    "RegimeAware_Heuristic": {"best": "all", "ok": ["bull", "bear", "sideways"]},
}


def detect_regime_hmm(
    symbol: str, db_path: str = DB_PATH, timeframe: str = "1d", lookback: int = 120
) -> Optional[dict]:
    """HMM-based regime detection using GaussianHMM with 3 states.

    Features: log returns, 20-day rolling volatility, 20-day rolling mean return.
    States are mapped to bull/bear/sideways by the mean return of each state.

    Returns dict matching detect_regime() format, or None if HMM cannot run.
    """
    if not HMM_AVAILABLE:
        return None

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, close FROM candlesticks "
        "WHERE symbol=? AND timeframe=? ORDER BY timestamp DESC LIMIT ?",
        conn,
        params=(symbol, timeframe, lookback + 50),
    )
    conn.close()

    if df.empty or len(df) < 120:
        return None  # insufficient data for HMM

    df = df.iloc[::-1].reset_index(drop=True)
    close = df["close"].astype(float)

    # Feature engineering
    log_returns = np.log(close / close.shift(1)).dropna()
    rolling_vol_20d = log_returns.rolling(20).std()
    rolling_mean_ret_20d = log_returns.rolling(20).mean()

    # Build feature matrix (drop NaN rows from rolling windows)
    features_df = pd.DataFrame(
        {
            "log_return": log_returns,
            "rolling_vol_20d": rolling_vol_20d,
            "rolling_mean_ret_20d": rolling_mean_ret_20d,
        }
    ).dropna()

    if len(features_df) < 120:
        return None  # still not enough after dropping NaN

    X = features_df.values  # shape: (n_samples, 3)

    # Check cache — reuse model if fitted on similar data size recently
    cache_key = f"{symbol}_{timeframe}"
    cached = _hmm_cache.get(cache_key)
    refit = True
    if cached:
        age_minutes = (datetime.now() - cached["fitted_at"]).total_seconds() / 60
        # Refit every 6 hours or if data size changed significantly
        if age_minutes < 360 and abs(cached["n_samples"] - len(X)) < 10:
            refit = False

    if refit:
        model = GaussianHMM(
            n_components=3,
            covariance_type="full",
            n_iter=100,
            random_state=42,
        )
        model.fit(X)
        _hmm_cache[cache_key] = {
            "model": model,
            "fitted_at": datetime.now(),
            "n_samples": len(X),
        }
    else:
        model = cached["model"]

    # Predict hidden states and get posterior probabilities
    hidden_states = model.predict(X)
    posteriors = model.predict_proba(X)

    # Map states to bull/bear/sideways by mean return of each state
    # means_[:, 0] is the mean log return for each state
    state_mean_returns = model.means_[:, 0]  # log_return is feature 0
    sorted_states = np.argsort(state_mean_returns)  # ascending
    state_map = {
        int(sorted_states[0]): "bear",  # lowest mean return
        int(sorted_states[1]): "sideways",  # middle mean return
        int(sorted_states[2]): "bull",  # highest mean return
    }

    # Current state = last observation
    current_state = int(hidden_states[-1])
    regime = state_map[current_state]
    confidence = float(posteriors[-1, current_state])

    details = {
        "method": "hmm",
        "state_means": {
            state_map[i]: round(float(model.means_[i, 0]), 6) for i in range(3)
        },
        "state_vols": {
            state_map[i]: round(float(model.means_[i, 1]), 6) for i in range(3)
        },
        "posterior": {
            state_map[i]: round(float(posteriors[-1, i]), 4) for i in range(3)
        },
        "n_samples": len(X),
        "hmm_score": round(float(model.score(X)), 2),
    }

    return {"regime": regime, "confidence": confidence, "details": details}


def detect_regime(
    symbol: str, db_path: str = DB_PATH, timeframe: str = "1d", lookback: int = 120
) -> dict:
    """Detect the current market regime for a symbol.

    Tries HMM-based detection first; falls back to heuristic scoring if HMM
    fails, is unavailable, or has insufficient data.

    Returns dict with:
      - regime: "bull", "bear", or "sideways"
      - confidence: 0-1
      - details: dict of supporting indicators
    """
    # Try HMM first
    try:
        hmm_result = detect_regime_hmm(symbol, db_path, timeframe, lookback)
        if hmm_result is not None:
            logger.info(
                f"[{symbol}] HMM regime: {hmm_result['regime']} "
                f"(conf={hmm_result['confidence']:.2f})"
            )
            return hmm_result
    except Exception as e:
        logger.warning(
            f"[{symbol}] HMM detection failed, falling back to heuristic: {e}"
        )

    # Heuristic fallback
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candlesticks WHERE symbol=? AND timeframe=? "
        "ORDER BY timestamp DESC LIMIT ?",
        conn,
        params=(symbol, timeframe, lookback + 50),
    )
    conn.close()

    if df.empty or len(df) < 60:
        return {"regime": "sideways", "confidence": 0.3, "details": {}}

    df = df.iloc[::-1].reset_index(drop=True)
    df["date"] = pd.to_datetime(df["timestamp"], unit="s")
    df = calculate_all_indicators(df)

    close = df["close"]
    bar = df.iloc[-1]

    # ── Indicators ──────────────────────────────────────────────────
    adx = bar.get("adx_14", 20)
    if adx is None or pd.isna(adx):
        adx = 20

    sma50 = bar.get("sma_50")
    sma200 = bar.get("sma_200")
    rsi = bar.get("rsi_14", 50)
    macd_h = bar.get("macd_histogram", 0)

    # SMA slope (20-day)
    sma50_series = df["sma_50"].dropna()
    if len(sma50_series) >= 20:
        sma_slope = (
            sma50_series.iloc[-1] - sma50_series.iloc[-20]
        ) / sma50_series.iloc[-20]
    else:
        sma_slope = 0

    # Price position relative to SMAs
    price = close.iloc[-1]
    above_sma50 = price > sma50 if sma50 and not pd.isna(sma50) else None
    above_sma200 = price > sma200 if sma200 and not pd.isna(sma200) else None

    # Volatility
    returns = close.pct_change().dropna()
    vol_20d = returns.iloc[-20:].std() * np.sqrt(365) if len(returns) >= 20 else 0.5
    vol_60d = returns.iloc[-60:].std() * np.sqrt(365) if len(returns) >= 60 else 0.5

    # 30-day and 90-day returns
    ret_30d = (price / close.iloc[-31]) - 1 if len(close) > 31 else 0
    ret_90d = (price / close.iloc[-91]) - 1 if len(close) > 91 else 0

    # ── Scoring ──────────────────────────────────────────────────────
    bull_score = 0
    bear_score = 0
    sideways_score = 0

    # ADX: trend strength
    if adx > 25:
        if sma_slope > 0.01:
            bull_score += 2
        elif sma_slope < -0.01:
            bear_score += 2
    else:
        sideways_score += 2

    # Price vs SMAs
    if above_sma50 is True:
        bull_score += 1
    elif above_sma50 is False:
        bear_score += 1
    if above_sma200 is True:
        bull_score += 1
    elif above_sma200 is False:
        bear_score += 1

    # Returns
    if ret_30d > 0.05:
        bull_score += 2
    elif ret_30d < -0.05:
        bear_score += 2
    else:
        sideways_score += 1

    if ret_90d > 0.10:
        bull_score += 1
    elif ret_90d < -0.10:
        bear_score += 1

    # MACD
    if macd_h and macd_h > 0:
        bull_score += 1
    elif macd_h and macd_h < 0:
        bear_score += 1

    # RSI
    if rsi and rsi > 60:
        bull_score += 1
    elif rsi and rsi < 40:
        bear_score += 1
    else:
        sideways_score += 1

    # Volatility regime
    if vol_20d > vol_60d * 1.3:
        bear_score += 1  # rising vol often bearish

    # ── Classification ───────────────────────────────────────────────
    scores = {"bull": bull_score, "bear": bear_score, "sideways": sideways_score}
    total = sum(scores.values()) or 1
    regime = max(scores, key=scores.get)
    confidence = scores[regime] / total

    details = {
        "method": "heuristic",
        "adx": round(adx, 1),
        "sma_slope": round(sma_slope, 4),
        "ret_30d": round(ret_30d, 4),
        "ret_90d": round(ret_90d, 4),
        "vol_20d": round(vol_20d, 4),
        "rsi": round(rsi, 1) if rsi else None,
        "scores": scores,
    }

    return {"regime": regime, "confidence": confidence, "details": details}


def strategy_fits_regime(strategy_name: str, regime: str) -> bool:
    """Check if a strategy is appropriate for the given regime."""
    fit = STRATEGY_REGIME_FIT.get(strategy_name)
    if not fit:
        return True  # unknown strategy — don't swap
    if fit["best"] == "all":
        return True  # adaptive strategies fit all regimes
    return fit["best"] == regime or regime in fit.get("ok", [])


def best_strategy_for_regime(
    symbol: str,
    regime: str,
    db_path: str = DB_PATH,
    min_trades: int = 2,
) -> Optional[dict]:
    """Query the catalogue for the best strategy for a symbol+regime combination.

    Uses catalogue results filtered by strategies that fit the target regime,
    ranked by Sharpe ratio.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all catalogue entries for this symbol
    rows = conn.execute(
        """
        SELECT strategy_name, sharpe_ratio, total_return, max_drawdown,
               win_rate, profit_factor, total_trades
        FROM strategy_catalogue
        WHERE symbol = ? AND total_trades >= ?
        ORDER BY sharpe_ratio DESC
    """,
        (symbol, min_trades),
    ).fetchall()
    conn.close()

    if not rows:
        return None

    # Filter to strategies that fit the regime
    for r in rows:
        name = r["strategy_name"]
        if strategy_fits_regime(name, regime):
            return dict(r)

    # No regime-fit strategy found — fall back to best overall
    return dict(rows[0]) if rows else None


def check_and_swap(
    portfolio_id: int = 1,
    db_path: str = DB_PATH,
    dry_run: bool = False,
    min_confidence: float = 0.4,
) -> List[dict]:
    """Main entry point: detect regimes, check fits, swap where needed.

    Returns list of swap actions taken.
    """
    from backtest.deployer import swap_strategy, get_strategy_id_map

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    portfolio = conn.execute(
        "SELECT strategy_routes_json, symbols_json, name FROM portfolios WHERE id = ?",
        (portfolio_id,),
    ).fetchone()
    conn.close()

    if not portfolio:
        logger.error(f"Portfolio {portfolio_id} not found")
        return []

    routes = json.loads(portfolio["strategy_routes_json"] or "{}")
    symbols = json.loads(portfolio["symbols_json"] or "[]")
    pf_name = portfolio["name"]

    swaps = []
    for symbol in symbols:
        current = routes.get(symbol, {})
        current_strategy = current.get("strategy")
        current_timeframe = current.get("timeframe", "1d")

        # Detect regime
        regime_result = detect_regime(symbol, db_path)
        regime = regime_result["regime"]
        confidence = regime_result["confidence"]

        logger.info(
            f"[{symbol}] regime={regime} (conf={confidence:.2f}) "
            f"current={current_strategy or 'none'}"
        )

        # Store regime result in market_regime table
        try:
            store_conn = sqlite3.connect(db_path)
            today = datetime.now().strftime("%Y-%m-%d")
            store_conn.execute(
                "INSERT OR REPLACE INTO market_regime (date, regime) VALUES (?, ?)",
                (today, regime),
            )
            store_conn.commit()
            store_conn.close()
        except Exception as e:
            logger.warning(f"[{symbol}] Failed to store regime in market_regime: {e}")

        # Skip low-confidence detections
        if confidence < min_confidence:
            logger.info(f"[{symbol}] Confidence too low ({confidence:.2f}), skipping")
            continue

        # Check if current strategy fits the regime
        if current_strategy and strategy_fits_regime(current_strategy, regime):
            continue  # no swap needed

        # Find best strategy for this regime
        best = best_strategy_for_regime(symbol, regime, db_path)
        if not best:
            logger.info(f"[{symbol}] No catalogued strategy for regime={regime}")
            continue

        new_strategy = best["strategy_name"]

        # Don't swap to the same strategy
        if new_strategy == current_strategy:
            continue

        reason = (
            f"regime={regime} (conf={confidence:.2f}) | "
            f"catalogue sharpe={best['sharpe_ratio']:.2f} "
            f"return={best['total_return']:.2%}"
        )

        if dry_run:
            swaps.append(
                {
                    "symbol": symbol,
                    "from": current_strategy,
                    "to": new_strategy,
                    "regime": regime,
                    "confidence": confidence,
                    "reason": reason,
                    "dry_run": True,
                }
            )
            logger.info(
                f"[DRY RUN] Would swap {symbol}: {current_strategy} → {new_strategy}"
            )
        else:
            result = swap_strategy(
                symbol=symbol,
                new_strategy=new_strategy,
                portfolio_id=portfolio_id,
                timeframe=current_timeframe,
                reason=reason,
                db_path=db_path,
            )
            result["regime"] = regime
            result["confidence"] = confidence
            swaps.append(result)

    if swaps:
        logger.info(
            f"[{pf_name}] {len(swaps)} strategy swaps {'proposed' if dry_run else 'executed'}"
        )
    else:
        logger.info(f"[{pf_name}] All strategies fit current regimes — no swaps needed")

    return swaps


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Regime Monitor — adaptive strategy routing"
    )
    parser.add_argument("--portfolio", type=int, default=1, help="Portfolio ID")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show proposed swaps without executing"
    )
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="Just show regime detection, no swaps",
    )
    parser.add_argument("--symbol", type=str, help="Check a single symbol")
    args = parser.parse_args()

    if args.detect_only or args.symbol:
        symbols = (
            [args.symbol]
            if args.symbol
            else json.loads(
                sqlite3.connect(DB_PATH)
                .execute(
                    "SELECT symbols_json FROM portfolios WHERE id=?", (args.portfolio,)
                )
                .fetchone()[0]
                or "[]"
            )
        )
        print(
            f"\n{'Symbol':12s} {'Regime':10s} {'Conf':6s} {'ADX':5s} {'30d':8s} {'90d':8s} {'Vol':6s}"
        )
        print("-" * 60)
        for sym in symbols:
            r = detect_regime(sym)
            d = r["details"]
            print(
                f"{sym:12s} {r['regime']:10s} {r['confidence']:5.2f} "
                f"{d.get('adx', 0):5.1f} {d.get('ret_30d', 0):7.2%} "
                f"{d.get('ret_90d', 0):7.2%} {d.get('vol_20d', 0):5.2f}"
            )
        return

    swaps = check_and_swap(
        portfolio_id=args.portfolio,
        dry_run=args.dry_run,
    )

    if swaps:
        print(f"\n{'Symbol':12s} {'From':25s} {'To':25s} {'Regime':10s}")
        print("-" * 75)
        for s in swaps:
            print(
                f"{s.get('symbol', '?'):12s} {str(s.get('from') or 'none'):25s} "
                f"{str(s.get('to') or '?'):25s} {str(s.get('regime') or '?'):10s}"
            )


if __name__ == "__main__":
    main()
