#!/usr/bin/env python3
"""
Portfolio Optimizer — true mean-variance optimization for Edoras.

Replaces the previous scoring-only implementation with actual portfolio
optimization. Three methods are available:

  - max_sharpe     : Maximum Sharpe ratio (default) — Markowitz mean-variance
                     with long-only constraints and per-asset caps.
  - min_variance   : Minimum global variance — similar to max_sharpe but
                     ignores expected returns, only minimises volatility.
  - risk_parity    : Inverse-volatility weighted — simple, robust fallback
                     that equalises risk contribution across assets.

The solver uses scipy.optimize.minimize (SLSQP) with a fallback to the
analytical maximum-Sharpe approximation (cov_inv @ mu) when scipy is
unavailable.  If optimisation fails for any reason the method falls back
to inverse-vol weighting so callers always receive a usable weight dict.

Primary public API:

    opt = PortfolioOptimizer(db_path)
    weights = opt.get_optimal_weights(
        method="max_sharpe",          # or "min_variance" / "risk_parity"
        symbols=["BTC-USD", ...],     # None → score-based universe selection
        constraints={"max_position": 0.30, "max_meme": 0.10},
    )
    # -> {"BTC-USD": 0.35, "ETH-USD": 0.28, ...}

Backwards-compatible alias:

    EnhancedPortfolioOptimizer = PortfolioOptimizer
"""

import logging
import os
import json
import sqlite3

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from config import DB_PATH as _CONFIG_DB_PATH
except ImportError:
    _CONFIG_DB_PATH = "crypto_data.db"

# ── Constants ────────────────────────────────────────────────────────────────

_LOOKBACK_DAYS = 365  # daily returns window for covariance estimation
_ANNUALISE = 365  # annualisation factor (crypto trades 365d/yr)
_MIN_DATA_DAYS = 30  # minimum days required before optimising a symbol
_MIN_ASSETS = 2  # minimum assets needed for covariance optimisation
_EPSILON = 1e-8  # numerical stability floor for zero-vol assets


# ── Categorisation map (shared with config.SYMBOL_TIERS where possible) ─────

_LARGE_CAP = {
    "BTC-USD",
    "ETH-USD",
    "BNB-USD",
    "SOL-USD",
    "XRP-USD",
    "ADA-USD",
    "AVAX-USD",
    "DOT-USD",
    "LINK-USD",
    "MATIC-USD",
    "ATOM-USD",
    "LTC-USD",
    "UNI-USD",
    "NEAR-USD",
    "TRX-USD",
    "ETC-USD",
    "XLM-USD",
    "ALGO-USD",
}

_MEME = {
    "DOGE-USD",
    "SHIB-USD",
    "BONK-USD",
    "TROLL-USD",
    "PEPE-USD",
    "FLOKI-USD",
    "WIF-USD",
}

_DEFI = {
    "UNI-USD",
    "AAVE-USD",
    "COMP-USD",
    "MKR-USD",
    "SNX-USD",
    "YFI-USD",
    "CRV-USD",
    "BAL-USD",
    "SUSHI-USD",
    "1INCH-USD",
}

_LAYER1 = {
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "ADA-USD",
    "AVAX-USD",
    "DOT-USD",
    "ATOM-USD",
    "ALGO-USD",
    "NEAR-USD",
    "ICP-USD",
}

_GAMING = {"SAND-USD", "MANA-USD", "GALA-USD", "ENJ-USD", "AXS-USD", "IMX-USD"}

_AI = {"FET-USD", "AGIX-USD", "OCEAN-USD", "NMR-USD"}


# ── Solver ───────────────────────────────────────────────────────────────────


def _returns_matrix(
    symbols: List[str],
    db_path: str,
    days: int = _LOOKBACK_DAYS,
) -> pd.DataFrame:
    """
    Load aligned daily returns for every symbol in *symbols*.
    Symbols with fewer than _MIN_DATA_DAYS observations are dropped.
    Returns a DataFrame of shape (days, n_valid_symbols).
    """
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp())
    conn = sqlite3.connect(db_path)
    frames: Dict[str, pd.Series] = {}

    for sym in symbols:
        try:
            df = pd.read_sql_query(
                "SELECT timestamp, close FROM candlesticks "
                "WHERE symbol=? AND timeframe='1d' AND timestamp>=? ORDER BY timestamp",
                conn,
                params=(sym, cutoff),
            )
            if len(df) < _MIN_DATA_DAYS:
                continue
            df["date"] = pd.to_datetime(df["timestamp"], unit="s").dt.normalize()
            df = df.drop_duplicates(subset="date", keep="last").set_index("date")
            ret = df["close"].pct_change().dropna()
            frames[sym] = ret
        except Exception as e:
            logger.debug(f"Returns load failed for {sym}: {e}")

    conn.close()

    if not frames:
        return pd.DataFrame()

    return pd.DataFrame(frames).dropna()


def _apply_constraints(
    raw_weights: np.ndarray,
    symbols: List[str],
    max_position: float = 0.30,
    max_meme: float = 0.10,
    min_large_cap: float = 0.40,
) -> np.ndarray:
    """
    Apply per-asset cap, meme cap, and large-cap floor to a raw weight vector.
    All weights are normalised to sum to 1.0 after each constraint.
    """
    w = np.array(raw_weights, dtype=float)

    # Per-position cap
    w = np.minimum(w, max_position)
    total = w.sum()
    if total > 0:
        w /= total

    # Meme cap: clamp combined meme weight to max_meme
    meme_idx = [i for i, s in enumerate(symbols) if s in _MEME]
    if meme_idx:
        meme_total = w[meme_idx].sum()
        if meme_total > max_meme + _EPSILON:
            scale = max_meme / meme_total
            w[meme_idx] *= scale
            # Redistribute freed weight to non-meme proportionally
            freed = meme_total - max_meme
            non_meme_idx = [i for i in range(len(symbols)) if i not in meme_idx]
            if non_meme_idx:
                non_meme_total = w[non_meme_idx].sum()
                if non_meme_total > _EPSILON:
                    w[non_meme_idx] += freed * w[non_meme_idx] / non_meme_total

    # Re-normalise
    total = w.sum()
    if total > 0:
        w /= total

    # Large-cap floor
    large_idx = [i for i, s in enumerate(symbols) if s in _LARGE_CAP]
    if large_idx and min_large_cap > 0:
        large_total = w[large_idx].sum()
        if large_total < min_large_cap - _EPSILON:
            deficit = min_large_cap - large_total
            non_large_idx = [i for i in range(len(symbols)) if i not in large_idx]
            if non_large_idx:
                non_large_total = w[non_large_idx].sum()
                take = min(deficit, non_large_total * 0.9)  # don't zero out non-large-cap
                if non_large_total > _EPSILON:
                    w[non_large_idx] -= take * w[non_large_idx] / non_large_total
                if large_total > _EPSILON:
                    w[large_idx] += take * w[large_idx] / large_total
                else:
                    # Distribute equally to large-cap
                    w[large_idx] += take / len(large_idx)

    # Final normalisation
    total = w.sum()
    return w / total if total > _EPSILON else np.ones(len(w)) / len(w)


def _solve_max_sharpe(
    mu: np.ndarray,
    cov: np.ndarray,
    symbols: List[str],
    max_position: float,
    max_meme: float,
    min_large_cap: float,
    risk_free_rate: float = 0.05,
) -> np.ndarray:
    """
    Solve for maximum Sharpe ratio weights (long-only, SLSQP).
    Falls back to analytical approximation then inverse-vol on failure.
    """
    n = len(symbols)

    # ── Try scipy SLSQP ─────────────────────────────────────────────────────
    try:
        from scipy.optimize import minimize

        def neg_sharpe(w: np.ndarray) -> float:
            port_ret = float(mu @ w)
            port_vol = float(np.sqrt(w @ cov @ w))
            if port_vol < _EPSILON:
                return 0.0
            return -(port_ret - risk_free_rate) / port_vol

        bounds = [(0.0, max_position)] * n
        constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        x0 = np.ones(n) / n

        result = minimize(
            neg_sharpe,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 500},
        )
        if result.success and result.x is not None:
            w = np.maximum(result.x, 0)
            w /= w.sum() if w.sum() > _EPSILON else 1
            return _apply_constraints(w, symbols, max_position, max_meme, min_large_cap)

    except ImportError:
        logger.debug("scipy not available; falling back to analytical max-Sharpe")
    except Exception as e:
        logger.debug(f"SLSQP failed: {e}; falling back to analytical solution")

    # ── Analytical approximation: cov_inv @ (mu - rf) ───────────────────────
    try:
        excess = mu - risk_free_rate / _ANNUALISE
        cov_inv = np.linalg.inv(cov)
        raw_w = cov_inv @ excess
        raw_w = np.maximum(raw_w, 0)
        total = raw_w.sum()
        if total > _EPSILON:
            return _apply_constraints(raw_w / total, symbols, max_position, max_meme, min_large_cap)
    except np.linalg.LinAlgError:
        logger.debug("Singular covariance; using inverse-vol fallback")

    # ── Inverse-vol fallback ─────────────────────────────────────────────────
    vols = np.sqrt(np.diag(cov))
    inv_vol = 1.0 / np.maximum(vols, _EPSILON)
    return _apply_constraints(
        inv_vol / inv_vol.sum(), symbols, max_position, max_meme, min_large_cap
    )


def _solve_min_variance(
    cov: np.ndarray,
    symbols: List[str],
    max_position: float,
    max_meme: float,
    min_large_cap: float,
) -> np.ndarray:
    """Solve for global minimum variance portfolio (long-only, SLSQP)."""
    n = len(symbols)

    try:
        from scipy.optimize import minimize

        def port_variance(w: np.ndarray) -> float:
            return float(w @ cov @ w)

        bounds = [(0.0, max_position)] * n
        constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        x0 = np.ones(n) / n

        result = minimize(
            port_variance,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 500},
        )
        if result.success and result.x is not None:
            w = np.maximum(result.x, 0)
            w /= w.sum() if w.sum() > _EPSILON else 1
            return _apply_constraints(w, symbols, max_position, max_meme, min_large_cap)
    except Exception as e:
        logger.debug(f"Min-variance SLSQP failed: {e}")

    # Fallback: inverse-vol
    vols = np.sqrt(np.diag(cov))
    inv_vol = 1.0 / np.maximum(vols, _EPSILON)
    return _apply_constraints(
        inv_vol / inv_vol.sum(), symbols, max_position, max_meme, min_large_cap
    )


def _solve_risk_parity(
    cov: np.ndarray,
    symbols: List[str],
    max_position: float,
    max_meme: float,
    min_large_cap: float,
) -> np.ndarray:
    """Simple inverse-volatility risk parity weights."""
    vols = np.sqrt(np.diag(cov))
    inv_vol = 1.0 / np.maximum(vols, _EPSILON)
    return _apply_constraints(
        inv_vol / inv_vol.sum(), symbols, max_position, max_meme, min_large_cap
    )


# ── Main class ───────────────────────────────────────────────────────────────


class PortfolioOptimizer:
    """
    True mean-variance portfolio optimizer for Edoras.

    Provides score-based universe selection, daily-returns covariance
    estimation, and three optimisation methods:
      - max_sharpe   (default)
      - min_variance
      - risk_parity

    Usage::

        opt = PortfolioOptimizer(db_path=config.DB_PATH)
        weights = opt.get_optimal_weights()  # -> {"BTC-USD": 0.32, ...}
    """

    def __init__(
        self,
        db_path: str = _CONFIG_DB_PATH,
        # kept for backwards compat with old callers that passed api_key/secret
        api_key: str = None,
        api_secret: str = None,
    ):
        self.db_path = db_path

        # Lazy: scorer and universe built on first use
        self._scorer = None
        self._universe = None

        self.available_symbols = self._load_available_symbols()
        self.categorized_symbols = self._categorize_symbols(self.available_symbols)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_available_symbols(self) -> List[str]:
        json_file = "coinbase_usd_pairs.json"
        if os.path.exists(json_file):
            with open(json_file) as f:
                return json.load(f)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM candlesticks WHERE symbol LIKE '%-USD'")
        syms = [r[0] for r in cur.fetchall()]
        conn.close()
        return syms

    def _categorize_symbols(self, symbols: List[str]) -> Dict[str, List[str]]:
        categories = {
            "large_cap": [],
            "mid_cap": [],
            "small_cap": [],
            "defi": [],
            "meme": [],
            "layer1": [],
            "gaming": [],
            "ai": [],
        }
        for sym in symbols:
            if sym in _LARGE_CAP:
                categories["large_cap"].append(sym)
            if sym in _DEFI:
                categories["defi"].append(sym)
            if sym in _MEME:
                categories["meme"].append(sym)
            if sym in _LAYER1:
                categories["layer1"].append(sym)
            if sym in _GAMING:
                categories["gaming"].append(sym)
            if sym in _AI:
                categories["ai"].append(sym)
            if sym not in _LARGE_CAP:
                if len(categories["mid_cap"]) < 30:
                    categories["mid_cap"].append(sym)
                else:
                    categories["small_cap"].append(sym)
        return categories

    @property
    def scorer(self):
        if self._scorer is None:
            from scoring.advanced_scorer import AdvancedScoringModel

            self._scorer = AdvancedScoringModel(self.db_path)
        return self._scorer

    @property
    def expanded_universe(self) -> List[str]:
        if self._universe is None:
            self._universe = self.select_diverse_universe()
        return self._universe

    # ── Universe selection ───────────────────────────────────────────────────

    def get_portfolio_symbols(self) -> List[str]:
        """Read active symbols from the portfolios table."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("SELECT symbols_json FROM portfolios WHERE is_active=1").fetchall()
            conn.close()
            syms = []
            for (j,) in rows:
                if j:
                    syms.extend(json.loads(j))
            return list(dict.fromkeys(syms))  # deduplicate, preserve order
        except Exception:
            # Hardcoded fallback
            return ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]

    def select_diverse_universe(self, max_symbols: int = 35) -> List[str]:
        """Score-driven diverse universe (same logic as before)."""
        selected: set = set(self.get_portfolio_symbols())
        cats = self.categorized_symbols

        selected.update(cats.get("large_cap", [])[:10])
        mid_cap = cats.get("mid_cap", [])
        if len(mid_cap) > 5:
            import random

            selected.update(random.sample(mid_cap, min(5, len(mid_cap))))
        else:
            selected.update(mid_cap)
        selected.update(cats.get("defi", [])[:3])
        selected.update(cats.get("gaming", [])[:2])
        selected.update(cats.get("ai", [])[:2])
        selected.update(cats.get("meme", [])[:2])
        layer1_extra = [s for s in cats.get("layer1", []) if s not in selected]
        selected.update(layer1_extra[:3])

        result = sorted(selected)[:max_symbols]
        logger.info(f"Optimiser universe: {len(result)} symbols")
        return result

    # ── Optimisation entry point ─────────────────────────────────────────────

    def get_optimal_weights(
        self,
        method: str = "max_sharpe",
        symbols: Optional[List[str]] = None,
        constraints: Optional[Dict] = None,
        lookback_days: int = _LOOKBACK_DAYS,
    ) -> Dict[str, float]:
        """
        Compute and return optimal portfolio weights.

        Parameters
        ----------
        method : "max_sharpe" | "min_variance" | "risk_parity"
        symbols : asset list to consider (None → expanded_universe)
        constraints : dict with optional keys:
            max_position  (float, default 0.30)
            max_meme      (float, default 0.10)
            min_large_cap (float, default 0.40)
        lookback_days : daily returns window (default 365)

        Returns
        -------
        Dict[str, float]
            {symbol: weight, ...} summing to ~1.0.
            Falls back to equal-weight on complete data failure.
        """
        constraints = constraints or {}
        max_position = float(constraints.get("max_position", 0.30))
        max_meme = float(constraints.get("max_meme", 0.10))
        min_large_cap = float(constraints.get("min_large_cap", 0.40))

        if symbols is None:
            symbols = self.expanded_universe

        # Build return matrix
        ret_df = _returns_matrix(symbols, self.db_path, days=lookback_days)

        if ret_df.empty or len(ret_df.columns) < _MIN_ASSETS:
            logger.warning("Insufficient return data — returning equal-weight")
            n = len(symbols)
            return {s: round(1.0 / n, 4) for s in symbols}

        valid_symbols = list(ret_df.columns)

        if len(valid_symbols) < _MIN_ASSETS:
            n = len(valid_symbols)
            return {s: round(1.0 / n, 4) for s in valid_symbols}

        mu = ret_df.mean().values * _ANNUALISE
        cov = ret_df.cov().values * _ANNUALISE

        if method == "max_sharpe":
            w = _solve_max_sharpe(mu, cov, valid_symbols, max_position, max_meme, min_large_cap)
        elif method == "min_variance":
            w = _solve_min_variance(cov, valid_symbols, max_position, max_meme, min_large_cap)
        elif method == "risk_parity":
            w = _solve_risk_parity(cov, valid_symbols, max_position, max_meme, min_large_cap)
        else:
            raise ValueError(
                f"Unknown method '{method}'. Use max_sharpe | min_variance | risk_parity"
            )

        weights = {s: round(float(v), 4) for s, v in zip(valid_symbols, w) if v > _EPSILON}

        # Normalise residual rounding errors
        total = sum(weights.values())
        if total > 0:
            weights = {s: round(v / total, 4) for s, v in weights.items()}

        logger.info(
            f"Optimised [{method}]: {len(weights)} positions, "
            f"top={max(weights, key=weights.get)} ({max(weights.values()):.1%})"
        )
        return weights

    # ── Backwards-compatible scoring methods (still used by paper_trading.py) ─

    def score_all_symbols(self) -> pd.DataFrame:
        """Score all symbols in the expanded universe (for backwards compat)."""
        scores = []
        for symbol in self.expanded_universe:
            try:
                score_data = self.scorer.calculate_total_score(symbol)
                score_data["symbol"] = symbol
                for cat, syms in self.categorized_symbols.items():
                    if symbol in syms:
                        score_data["category"] = cat
                        break
                else:
                    score_data["category"] = "other"
                scores.append(score_data)
            except Exception as e:
                logger.debug(f"Scoring failed for {symbol}: {e}")

        if not scores:
            return pd.DataFrame()

        df = pd.DataFrame(scores)
        wanted = [
            "symbol",
            "category",
            "total_score",
            "momentum",
            "trend",
            "volatility",
            "volume",
            "risk_adjusted",
        ]
        existing = [c for c in wanted if c in df.columns]
        df = df[existing].sort_values("total_score", ascending=False)
        return df

    def calculate_portfolio_risk_metrics(self, portfolio_symbols: List[str]) -> Dict:
        """Per-symbol risk metrics (Sharpe, Sortino, MaxDD, VaR, vol)."""
        if not portfolio_symbols:
            return {"symbols": [], "metrics": {}}

        conn = sqlite3.connect(self.db_path)
        metrics = {}

        for symbol in portfolio_symbols:
            try:
                df = pd.read_sql_query(
                    "SELECT timestamp, close FROM candlesticks "
                    "WHERE symbol=? AND timeframe='1d' ORDER BY timestamp",
                    conn,
                    params=(symbol,),
                )
                if len(df) < _MIN_DATA_DAYS:
                    continue

                returns = df["close"].pct_change().dropna()
                mu_d = returns.mean()
                sigma_d = returns.std()
                sharpe = mu_d / sigma_d * np.sqrt(_ANNUALISE) if sigma_d > 0 else 0.0
                down_std = returns[returns < 0].std()
                sortino = mu_d / down_std * np.sqrt(_ANNUALISE) if down_std > 0 else 0.0

                cum = (1 + returns).cumprod()
                max_dd = ((cum - cum.expanding().max()) / cum.expanding().max()).min()
                var_95 = float(np.percentile(returns, 5))

                metrics[symbol] = {
                    "sharpe_ratio": round(sharpe, 3),
                    "sortino_ratio": round(sortino, 3),
                    "max_drawdown": round(float(max_dd), 4),
                    "var_95": round(var_95, 4),
                    "volatility": round(sigma_d, 4),
                    "avg_return": round(mu_d, 4),
                }
            except Exception as e:
                logger.debug(f"Risk metrics failed for {symbol}: {e}")

        conn.close()
        return {"symbols": portfolio_symbols, "metrics": metrics}

    # ── Covariance matrix (direct access, used by other callers) ─────────────

    def covariance_matrix(
        self,
        symbols: Optional[List[str]] = None,
        lookback_days: int = _LOOKBACK_DAYS,
    ) -> pd.DataFrame:
        """
        Return the annualised return covariance matrix for *symbols*.
        Useful for callers that need raw covariance data.
        """
        if symbols is None:
            symbols = self.expanded_universe
        ret_df = _returns_matrix(symbols, self.db_path, days=lookback_days)
        if ret_df.empty:
            return pd.DataFrame()
        return ret_df.cov() * _ANNUALISE

    # ── Report generation ────────────────────────────────────────────────────

    def generate_enhanced_report(self) -> str:
        """Generate a portfolio optimization report with optimised weights."""
        lines = []
        lines.append("**Portfolio Optimisation Report**")
        lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        lines.append(f"**Universe:** {len(self.expanded_universe)} symbols")

        scores_df = self.score_all_symbols()
        if not scores_df.empty:
            lines.append("")
            lines.append("**Top 10 by Score**")
            for _, row in scores_df.head(10).iterrows():
                lines.append(
                    f"  {row['symbol']} ({row.get('category', '?')}): {row['total_score']:.0f}/100"
                )
            lines.append("")
            lines.append("**Bottom 5 by Score**")
            for _, row in scores_df.tail(5).iterrows():
                lines.append(f"  {row['symbol']}: {row['total_score']:.0f}/100")

        # Optimised weights
        lines.append("")
        lines.append("**Optimised Allocation (max-Sharpe)**")
        try:
            portfolio_syms = self.get_portfolio_symbols()
            weights = self.get_optimal_weights(
                method="max_sharpe",
                symbols=portfolio_syms or self.expanded_universe[:15],
            )
            for sym, w in sorted(weights.items(), key=lambda x: -x[1]):
                if w > 0.005:
                    lines.append(f"  {sym}: {w:.1%}")
        except Exception as e:
            lines.append(f"  (optimisation failed: {e})")

        # Risk metrics
        portfolio_symbols = self.get_portfolio_symbols()
        if portfolio_symbols:
            lines.append("")
            lines.append("**Portfolio Risk Metrics**")
            risk = self.calculate_portfolio_risk_metrics(portfolio_symbols)
            for sym, m in risk.get("metrics", {}).items():
                lines.append(
                    f"  {sym}: Sharpe={m['sharpe_ratio']:.2f}, "
                    f"MaxDD={m['max_drawdown']:.1%}, "
                    f"Vol={m['volatility']:.2%}"
                )

        lines.append("")
        lines.append("_Not financial advice_")
        return "\n".join(lines)


# Backwards-compatible alias (paper_trading.py and paper_rebalancing.py import this)
EnhancedPortfolioOptimizer = PortfolioOptimizer


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio Optimizer")
    parser.add_argument("--report", action="store_true", help="Generate report")
    parser.add_argument("--weights", action="store_true", help="Print optimal weights")
    parser.add_argument(
        "--method",
        default="max_sharpe",
        choices=["max_sharpe", "min_variance", "risk_parity"],
    )
    parser.add_argument("--symbols", nargs="+", default=None, help="Override symbol list")
    args = parser.parse_args()

    opt = PortfolioOptimizer(_CONFIG_DB_PATH)

    if args.weights or not args.report:
        print(f"\n=== Optimal Weights [{args.method}] ===")
        syms = args.symbols or opt.get_portfolio_symbols()
        weights = opt.get_optimal_weights(method=args.method, symbols=syms)
        for sym, w in sorted(weights.items(), key=lambda x: -x[1]):
            print(f"  {sym:20s} {w:6.1%}")

    if args.report:
        print("\n" + opt.generate_enhanced_report())


if __name__ == "__main__":
    main()
