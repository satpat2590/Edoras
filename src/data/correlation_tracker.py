#!/usr/bin/env python3
"""
Cross-asset correlation tracker and market regime detector.
Tracks BTC-equity correlations, VIX regime, and portfolio diversification metrics.
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    MIN_DAYS_FOR_CORRELATION,
    PORTFOLIO_SYMBOLS,
    EQUITY_SYMBOLS,
    INDEX_SYMBOLS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class CorrelationTracker:
    """Track cross-asset correlations and detect market regimes."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """Create correlation and regime tables if needed."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS correlations (
                date TEXT NOT NULL,
                symbol_a TEXT NOT NULL,
                symbol_b TEXT NOT NULL,
                window INTEGER NOT NULL,
                correlation REAL NOT NULL,
                PRIMARY KEY (date, symbol_a, symbol_b, window)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_regime (
                date TEXT PRIMARY KEY,
                vix_value REAL,
                regime TEXT,
                btc_sp500_corr REAL,
                btc_nasdaq_corr REAL
            )
        """)

        conn.commit()
        conn.close()

    # ── Data loading ─────────────────────────────────────────────────────

    def _get_daily_returns(self, symbol: str, days: int = 365) -> pd.Series:
        """Load daily close prices and return daily returns series."""
        conn = sqlite3.connect(self.db_path)
        cutoff = int((datetime.now() - timedelta(days=days)).timestamp())

        df = pd.read_sql_query(
            "SELECT timestamp, close FROM candlesticks "
            "WHERE symbol=? AND timeframe='1d' AND timestamp>=? ORDER BY timestamp",
            conn,
            params=(symbol, cutoff),
        )
        conn.close()

        if df.empty or len(df) < 5:
            return pd.Series(dtype=float)

        df["date"] = pd.to_datetime(df["timestamp"], unit="s").dt.normalize()
        df = df.drop_duplicates(subset="date", keep="last").set_index("date")
        return df["close"].pct_change().dropna()

    # ── Pairwise correlation ─────────────────────────────────────────────

    def rolling_correlation(
        self,
        symbol_a: str,
        symbol_b: str,
        window: int = 30,
        days: int = 365,
    ) -> pd.Series:
        """Compute rolling Pearson correlation between two symbols' daily returns."""
        ra = self._get_daily_returns(symbol_a, days)
        rb = self._get_daily_returns(symbol_b, days)

        if ra.empty or rb.empty:
            return pd.Series(dtype=float)

        # Align on common dates
        combined = pd.DataFrame({"a": ra, "b": rb}).dropna()

        if len(combined) < window:
            logger.warning(
                f"Insufficient overlap for {symbol_a}/{symbol_b}: {len(combined)} days"
            )
            return pd.Series(dtype=float)

        return combined["a"].rolling(window).corr(combined["b"]).dropna()

    def current_correlation(
        self, symbol_a: str, symbol_b: str, window: int = 30
    ) -> Optional[float]:
        """Get the most recent rolling correlation value."""
        series = self.rolling_correlation(symbol_a, symbol_b, window)
        if series.empty:
            return None
        return float(series.iloc[-1])

    def correlation_matrix(self, symbols: List[str], window: int = 30) -> pd.DataFrame:
        """Compute pairwise Pearson correlation matrix for a list of symbols."""
        returns = {}
        for sym in symbols:
            r = self._get_daily_returns(sym, days=window * 2)
            if not r.empty:
                returns[sym] = r

        if len(returns) < 2:
            return pd.DataFrame()

        df = pd.DataFrame(returns).dropna()
        if len(df) < window:
            return pd.DataFrame()

        return df.tail(window).corr()

    def covariance_matrix(
        self, symbols: List[str], window: int = 252, annualise: bool = True
    ) -> pd.DataFrame:
        """
        Compute the annualised return covariance matrix for a list of symbols.

        Parameters
        ----------
        symbols   : asset list
        window    : number of daily observations to use (default 252 ≈ 1 year)
        annualise : if True, multiply by 365 (crypto trades every day)

        Returns
        -------
        pd.DataFrame
            NxN covariance matrix.  Empty DataFrame if data is insufficient.
        """
        returns: dict = {}
        for sym in symbols:
            r = self._get_daily_returns(sym, days=window * 2)
            if not r.empty:
                returns[sym] = r

        if len(returns) < 2:
            return pd.DataFrame()

        df = pd.DataFrame(returns).dropna().tail(window)
        if len(df) < 30:
            return pd.DataFrame()

        cov = df.cov()
        return cov * 365 if annualise else cov

    # ── VIX regime detection ─────────────────────────────────────────────

    def get_vix_level(self) -> Optional[float]:
        """Get the latest VIX value from the database."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM candlesticks WHERE symbol='^VIX' AND timeframe='1d' "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else None

    def detect_regime(self) -> Tuple[str, Optional[float]]:
        """
        Detect market regime based on VIX level.
        Returns (regime_label, vix_value).
        Regimes: 'risk-on' (VIX < 20), 'neutral' (20-30), 'risk-off' (VIX > 30).
        """
        vix = self.get_vix_level()
        if vix is None:
            return "unknown", None

        if vix < 20:
            return "risk-on", vix
        elif vix <= 30:
            return "neutral", vix
        else:
            return "risk-off", vix

    def regime_signal_adjustment(self, base_strength: float, action: str) -> float:
        """
        Adjust signal strength based on current regime.
        In risk-off: dampen buy signals, amplify sell signals.
        In risk-on: amplify buy signals, dampen sell signals.
        """
        regime, vix = self.detect_regime()

        if regime == "risk-off":
            if action == "BUY":
                return base_strength * 0.5
            elif action == "SELL":
                return base_strength * 1.3
        elif regime == "risk-on":
            if action == "BUY":
                return base_strength * 1.2
            elif action == "SELL":
                return base_strength * 0.8

        return base_strength  # neutral / unknown

    # ── Key correlation pairs ────────────────────────────────────────────

    def btc_equity_correlations(self, window: int = 30) -> Dict[str, Optional[float]]:
        """Get BTC correlation with major equity indices."""
        return {
            "BTC-SPY": self.current_correlation("BTC-USD", "SPY", window),
            "BTC-QQQ": self.current_correlation("BTC-USD", "QQQ", window),
        }

    def find_decorrelated_assets(
        self,
        symbols: List[str] = None,
        max_corr: float = 0.3,
        window: int = 30,
    ) -> List[Tuple[str, str, float]]:
        """Find pairs of symbols with low or negative correlation (diversification opportunities)."""
        if symbols is None:
            symbols = PORTFOLIO_SYMBOLS[:6] + EQUITY_SYMBOLS[:4]

        matrix = self.correlation_matrix(symbols, window)
        if matrix.empty:
            return []

        pairs = []
        done = set()
        for i, s1 in enumerate(matrix.columns):
            for j, s2 in enumerate(matrix.columns):
                if i >= j:
                    continue
                key = (s1, s2)
                if key in done:
                    continue
                done.add(key)
                corr = matrix.loc[s1, s2]
                if abs(corr) <= max_corr:
                    pairs.append((s1, s2, round(corr, 3)))

        return sorted(pairs, key=lambda x: x[2])

    def portfolio_beta_vs_btc(
        self, portfolio_symbols: List[str] = None, window: int = 60
    ) -> Optional[float]:
        """
        Calculate portfolio beta relative to BTC.
        Beta = Cov(portfolio, BTC) / Var(BTC).
        Assumes equal-weight portfolio.
        """
        if portfolio_symbols is None:
            portfolio_symbols = PORTFOLIO_SYMBOLS

        btc_returns = self._get_daily_returns("BTC-USD", days=window * 2)
        if btc_returns.empty:
            return None

        port_returns_list = []
        for sym in portfolio_symbols:
            if sym == "BTC-USD":
                continue
            r = self._get_daily_returns(sym, days=window * 2)
            if not r.empty:
                port_returns_list.append(r)

        if not port_returns_list:
            return None

        # Equal-weight portfolio returns
        port_df = pd.DataFrame(port_returns_list).T.dropna()
        if port_df.empty:
            return None
        port_returns = port_df.mean(axis=1)

        combined = (
            pd.DataFrame({"port": port_returns, "btc": btc_returns})
            .dropna()
            .tail(window)
        )
        if len(combined) < MIN_DAYS_FOR_CORRELATION:
            return None

        cov = combined["port"].cov(combined["btc"])
        var_btc = combined["btc"].var()
        if var_btc == 0:
            return None

        return round(cov / var_btc, 3)

    # ── Persistence ──────────────────────────────────────────────────────

    def save_daily_snapshot(self):
        """Persist today's correlations and regime to the database."""
        today = datetime.now().strftime("%Y-%m-%d")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # Key correlations
        for window in [30, 60, 90]:
            for sym_a, sym_b in [
                ("BTC-USD", "SPY"),
                ("BTC-USD", "QQQ"),
                ("BTC-USD", "ETH-USD"),
            ]:
                corr = self.current_correlation(sym_a, sym_b, window)
                if corr is not None:
                    cur.execute(
                        "INSERT OR REPLACE INTO correlations (date, symbol_a, symbol_b, window, correlation) "
                        "VALUES (?,?,?,?,?)",
                        (today, sym_a, sym_b, window, corr),
                    )

        # Regime
        regime, vix = self.detect_regime()
        btc_spy = self.current_correlation("BTC-USD", "SPY", 30)
        btc_qqq = self.current_correlation("BTC-USD", "QQQ", 30)

        cur.execute(
            "INSERT OR REPLACE INTO market_regime (date, vix_value, regime, btc_sp500_corr, btc_nasdaq_corr) "
            "VALUES (?,?,?,?,?)",
            (today, vix, regime, btc_spy, btc_qqq),
        )

        conn.commit()
        conn.close()
        logger.info(
            f"Saved correlation snapshot for {today}: regime={regime}, VIX={vix}"
        )

    # ── Reporting ────────────────────────────────────────────────────────

    def generate_report(self) -> str:
        """Generate a human-readable cross-asset correlation report."""
        lines = []
        lines.append("🔗 **Cross-Asset Correlation Report**")
        lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        # Regime
        regime, vix = self.detect_regime()
        regime_emoji = {"risk-on": "🟢", "neutral": "🟡", "risk-off": "🔴"}.get(
            regime, "❓"
        )
        lines.append(f"**Market Regime:** {regime_emoji} {regime.upper()}")
        if vix is not None:
            lines.append(f"• VIX: {vix:.1f}")
        lines.append("")

        # BTC-equity correlations
        btc_corrs = self.btc_equity_correlations(30)
        lines.append("**BTC-Equity Correlations (30d):**")
        for label, val in btc_corrs.items():
            if val is not None:
                lines.append(f"• {label}: {val:.3f}")
            else:
                lines.append(f"• {label}: N/A (insufficient data)")
        lines.append("")

        # Portfolio beta
        beta = self.portfolio_beta_vs_btc()
        if beta is not None:
            lines.append(f"**Portfolio Beta vs BTC:** {beta:.2f}")
            if beta > 1.5:
                lines.append("  ⚠️ High beta — portfolio moves more than BTC")
            elif beta < 0.5:
                lines.append("  ✅ Low beta — good diversification from BTC")
        lines.append("")

        # Decorrelated opportunities
        pairs = self.find_decorrelated_assets()
        if pairs:
            lines.append("**Decorrelated Pairs (diversification opportunities):**")
            for s1, s2, corr in pairs[:5]:
                lines.append(f"• {s1} / {s2}: {corr:.3f}")
        lines.append("")

        lines.append("---")
        lines.append("_Cross-asset analysis for portfolio diversification_")
        return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Correlation Tracker")
    parser.add_argument(
        "--report", action="store_true", help="Generate correlation report"
    )
    parser.add_argument("--snapshot", action="store_true", help="Save daily snapshot")
    parser.add_argument(
        "--matrix", action="store_true", help="Print correlation matrix"
    )
    parser.add_argument(
        "--regime", action="store_true", help="Show current market regime"
    )
    args = parser.parse_args()

    tracker = CorrelationTracker()

    if args.report:
        print(tracker.generate_report())
    elif args.snapshot:
        tracker.save_daily_snapshot()
    elif args.matrix:
        symbols = PORTFOLIO_SYMBOLS[:5] + ["SPY", "QQQ"]
        matrix = tracker.correlation_matrix(symbols)
        if not matrix.empty:
            print("\nCorrelation Matrix (30d):")
            print(matrix.round(3).to_string())
        else:
            print("Insufficient data for correlation matrix")
    elif args.regime:
        regime, vix = tracker.detect_regime()
        print(f"Regime: {regime}")
        if vix is not None:
            print(f"VIX: {vix:.1f}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
