#!/usr/bin/env python3
"""
Exit Overlay — generates SELL signals for held positions based on
trend, momentum, volatility, and correlation conditions.

Runs AFTER entry strategies in the signal pipeline. Can generate
SELL signals for any held position regardless of which strategy
entered it.

The exit overlay does NOT generate BUY signals. It only exits.

Architecture:
  Entry strategy → decides WHEN to buy
  Exit overlay   → decides WHEN to sell (independent of entry strategy)
  Risk manager   → mechanical stops (last line of defense)

The exit overlay sits BETWEEN the entry strategy and the risk manager:
  - Smarter than mechanical stops (reads trend, momentum, correlation)
  - Less aggressive than the entry strategy's own exit logic
  - Catches regime transitions that the entry strategy doesn't handle
"""

import logging
import sqlite3
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta

try:
    from config import DB_PATH as _CONFIG_DB_PATH
except ImportError:
    _CONFIG_DB_PATH = "crypto_data.db"

logger = logging.getLogger(__name__)

EXIT_OVERLAY_DEFAULTS = {
    # Momentum exit
    "momentum_lookback_days": 21,
    "momentum_threshold": -0.05,       # -5% return triggers exit (increased from -2%)
    "momentum_min_held_hours": 48,     # Don't exit positions held < 48h (increased from 24h)

    # Trend break exit
    "trend_break_min_adx": 20,         # ADX must confirm trend has strength
    "trend_break_min_held_hours": 24,  # Don't exit very fresh positions (increased from 12h)

    # Volatility exit
    "volatility_expansion_threshold": 1.5,  # ATR must be 1.5x entry ATR

    # Correlation exit
    "correlation_threshold": 0.9,      # Rolling correlation with BTC (increased from 0.8)
    "correlation_window_days": 21,     # Window for rolling correlation (increased from 14)
    "cluster_loss_count": 4,           # How many losing positions = cluster (increased from 3)

    # Time deterioration
    "max_hold_days": 21,               # Exit losing positions after this (increased from 14)

    # General
    "min_loss_pct_to_exit": -0.02,     # Position must be at least -2% to trigger (increased from -0.5%)
}


class ExitOverlay:
    """Exit overlay that generates SELL signals for held positions."""

    def __init__(self, db_path: str = _CONFIG_DB_PATH, config: dict = None):
        self.db_path = db_path
        self.config = {**EXIT_OVERLAY_DEFAULTS, **(config or {})}

    def _get_indicator_window(self, symbol: str, timeframe: str = "4h",
                              lookback: int = 60) -> Optional[pd.DataFrame]:
        """Load the last `lookback` candles with indicators."""
        conn = sqlite3.connect(self.db_path)
        try:
            df = pd.read_sql_query(
                "SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume, "
                "i.rsi_14, i.macd_line, i.macd_signal, i.macd_histogram, "
                "i.sma_20, i.sma_50, i.sma_200, i.ema_12, i.ema_26, "
                "i.bb_upper, i.bb_middle, i.bb_lower, i.bb_width, "
                "i.atr_14, i.volume_sma_20, i.volume_ratio, i.adx_14 "
                "FROM candlesticks c "
                "JOIN indicators i ON c.symbol=i.symbol AND c.timeframe=i.timeframe "
                "AND c.timestamp=i.timestamp "
                "WHERE c.symbol=? AND c.timeframe=? "
                "ORDER BY c.timestamp DESC LIMIT ?",
                conn,
                params=(symbol, timeframe, lookback),
            )
        finally:
            conn.close()

        if df.empty or len(df) < 5:
            return None

        df = df.iloc[::-1].reset_index(drop=True)
        return df

    def _get_position_entry(self, symbol: str, portfolio_id: int = 1) -> Optional[dict]:
        """Get position entry details from the positions table."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT entry_time, entry_price, quantity, pnl_percent "
                "FROM positions WHERE portfolio_id=? AND symbol=? AND status='open'",
                (portfolio_id, symbol),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None

        return {
            "entry_time": row[0],
            "entry_price": row[1],
            "quantity": row[2],
            "pnl_percent": row[3],
        }

    def _hours_held(self, entry_time_str: str) -> float:
        """Calculate hours since position entry."""
        try:
            entry = datetime.fromisoformat(entry_time_str)
            delta = datetime.now() - entry
            return delta.total_seconds() / 3600
        except (ValueError, TypeError):
            return 0.0

    def _pnl_from_price(self, entry_price: float, current_price: float) -> float:
        """Calculate P&L percentage from entry and current price."""
        if entry_price <= 0:
            return 0.0
        return (current_price / entry_price) - 1.0

    def check_momentum_exit(self, symbol: str, df: pd.DataFrame,
                            position: dict) -> Optional[dict]:
        """
        TSMOM-style momentum exit.

        If the N-day return is negative AND the position is losing money,
        generate a SELL signal.
        """
        lookback = self.config["momentum_lookback_days"]
        threshold = self.config["momentum_threshold"]
        min_held = self.config["momentum_min_held_hours"]

        hours = self._hours_held(position.get("entry_time", ""))
        if hours < min_held:
            logger.debug(f"[ExitOverlay/{symbol}] momentum: held {hours:.1f}h < {min_held}h min — skip")
            return None

        close = df["close"]
        if len(close) < lookback + 1:
            logger.debug(f"[ExitOverlay/{symbol}] momentum: insufficient data ({len(close)} < {lookback+1})")
            return None

        mom_return = (close.iloc[-1] / close.iloc[-lookback]) - 1

        pnl = position.get("pnl_percent", 0) or 0
        # pnl_percent from database is percentage as number (e.g., -0.87 for -0.87%)
        # Convert to fraction for consistent comparison with thresholds
        pnl_frac = pnl / 100.0

        min_loss = self.config["min_loss_pct_to_exit"]

        if mom_return >= threshold:
            logger.debug(f"[ExitOverlay/{symbol}] momentum: {lookback}d return={mom_return:.2%} "
                        f">= {threshold:.2%} threshold — hold")
            return None

        if pnl_frac > min_loss:
            logger.debug(f"[ExitOverlay/{symbol}] momentum: P&L={pnl_frac:.2%} above min loss "
                        f"{min_loss:.2%} — hold")
            return None

        # Scale strength by how negative momentum is
        abs_mom = abs(mom_return)
        if abs_mom < 0.05:
            strength = 50 + (abs_mom / 0.05) * 15  # 50-65
        elif abs_mom < 0.10:
            strength = 65 + ((abs_mom - 0.05) / 0.05) * 15  # 65-80
        else:
            strength = 80 + min((abs_mom - 0.10) / 0.10, 1.0) * 20  # 80-100

        strength = min(strength, 100)

        reason = (f"Momentum exit: {lookback}d return={mom_return:.2%}, "
                  f"P&L={pnl_frac:.2%}, held {hours:.1f}h")
        logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")

        return {
            "symbol": symbol,
            "action": "SELL",
            "strength": round(strength, 1),
            "reason": reason,
            "exit_type": "momentum_exit",
            "_strategy_name": "exit_overlay",
            "_timeframe": "4h",
        }

    def check_trend_break_exit(self, symbol: str, df: pd.DataFrame,
                               position: dict) -> Optional[dict]:
        """
        Exit when trend structure breaks against the position.

        All of: price < SMA20, SMA20 < SMA50, MACD histogram negative, ADX > 20.
        """
        min_held = self.config["trend_break_min_held_hours"]
        min_adx = self.config["trend_break_min_adx"]

        hours = self._hours_held(position.get("entry_time", ""))
        if hours < min_held:
            logger.debug(f"[ExitOverlay/{symbol}] trend_break: held {hours:.1f}h < {min_held}h — skip")
            return None

        bar = df.iloc[-1]
        price = bar.get("close")
        sma20 = bar.get("sma_20")
        sma50 = bar.get("sma_50")
        macd_hist = bar.get("macd_histogram")
        adx = bar.get("adx_14")

        # Need all indicators present
        vals = [price, sma20, sma50, macd_hist, adx]
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in vals):
            logger.debug(f"[ExitOverlay/{symbol}] trend_break: missing indicators — skip")
            return None

        pnl = position.get("pnl_percent", 0) or 0
        pnl_frac = pnl / 100.0  # Convert percentage to fraction

        min_loss = self.config["min_loss_pct_to_exit"]
        if pnl_frac > min_loss:
            logger.debug(f"[ExitOverlay/{symbol}] trend_break: P&L={pnl_frac:.2%} above min loss — hold")
            return None

        conditions = {
            "price<SMA20": price < sma20,
            "SMA20<SMA50": sma20 < sma50,
            "MACD_neg": macd_hist < 0,
            "ADX>min": adx > min_adx,
        }
        met = {k: v for k, v in conditions.items() if v}
        not_met = {k: v for k, v in conditions.items() if not v}

        if len(met) < 4:
            logger.debug(f"[ExitOverlay/{symbol}] trend_break: {len(met)}/4 conditions met "
                        f"(missing: {', '.join(not_met.keys())}) — hold")
            return None

        # Strength based on ADX (trend strength)
        strength = 60 + min((adx - min_adx) / 20, 1.0) * 20  # 60-80

        reason = (f"Trend break: price({price:.4g})<SMA20({sma20:.4g})<SMA50({sma50:.4g}), "
                  f"ADX={adx:.1f}, MACD_h={macd_hist:.4f}, P&L={pnl_frac:.2%}")
        logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")

        return {
            "symbol": symbol,
            "action": "SELL",
            "strength": round(strength, 1),
            "reason": reason,
            "exit_type": "trend_break",
            "_strategy_name": "exit_overlay",
            "_timeframe": "4h",
        }

    def check_volatility_exit(self, symbol: str, df: pd.DataFrame,
                              position: dict) -> Optional[dict]:
        """
        Exit when volatility expands significantly against the position.

        If current ATR(14) > entry-time ATR * 1.5 AND position is losing.
        """
        threshold = self.config["volatility_expansion_threshold"]

        pnl = position.get("pnl_percent", 0) or 0
        pnl_frac = pnl / 100.0  # Convert percentage to fraction

        min_loss = self.config["min_loss_pct_to_exit"]
        if pnl_frac > min_loss:
            logger.debug(f"[ExitOverlay/{symbol}] volatility: P&L={pnl_frac:.2%} above min loss — hold")
            return None

        current_atr = df.iloc[-1].get("atr_14")
        if current_atr is None or (isinstance(current_atr, float) and np.isnan(current_atr)):
            logger.debug(f"[ExitOverlay/{symbol}] volatility: no ATR data — skip")
            return None

        # Find ATR near entry time
        entry_time_str = position.get("entry_time", "")
        entry_atr = None
        if entry_time_str:
            try:
                entry_dt = datetime.fromisoformat(entry_time_str)
                entry_ts = int(entry_dt.timestamp())
                # Find closest ATR to entry timestamp
                conn = sqlite3.connect(self.db_path)
                try:
                    row = conn.execute(
                        "SELECT atr_14 FROM indicators "
                        "WHERE symbol=? AND timeframe='4h' AND atr_14 IS NOT NULL "
                        "ORDER BY ABS(timestamp - ?) LIMIT 1",
                        (symbol, entry_ts),
                    ).fetchone()
                    if row:
                        entry_atr = row[0]
                finally:
                    conn.close()
            except (ValueError, TypeError):
                pass

        if entry_atr is None or entry_atr <= 0:
            # Fallback: use median ATR from the window
            atr_series = df["atr_14"].dropna()
            if len(atr_series) < 10:
                logger.debug(f"[ExitOverlay/{symbol}] volatility: insufficient ATR history — skip")
                return None
            entry_atr = atr_series.iloc[:len(atr_series)//2].median()

        if entry_atr <= 0:
            return None

        expansion = current_atr / entry_atr

        if expansion < threshold:
            logger.debug(f"[ExitOverlay/{symbol}] volatility: ATR expansion={expansion:.2f}x "
                        f"< {threshold}x threshold — hold")
            return None

        # Strength 55-70 based on expansion magnitude
        strength = 55 + min((expansion - threshold) / 1.0, 1.0) * 15

        reason = (f"Volatility spike: ATR expanded {expansion:.2f}x "
                  f"(current={current_atr:.4g}, entry={entry_atr:.4g}), P&L={pnl_frac:.2%}")
        logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")

        return {
            "symbol": symbol,
            "action": "SELL",
            "strength": round(strength, 1),
            "reason": reason,
            "exit_type": "volatility_spike",
            "_strategy_name": "exit_overlay",
            "_timeframe": "4h",
        }

    def check_correlation_exit(self, symbol: str, df: pd.DataFrame,
                               position: dict,
                               portfolio_positions: dict) -> Optional[dict]:
        """
        Exit when a symbol correlates with a losing BTC or when too many
        portfolio positions are losing simultaneously.
        """
        pnl = position.get("pnl_percent", 0) or 0
        pnl_frac = pnl / 100.0  # Convert percentage to fraction

        min_loss = self.config["min_loss_pct_to_exit"]
        if pnl_frac > min_loss:
            logger.debug(f"[ExitOverlay/{symbol}] correlation: P&L={pnl_frac:.2%} above min loss — hold")
            return None

        if symbol == "BTC-USD":
            # BTC can't correlate with itself; skip BTC correlation check
            # but still check cluster loss
            pass
        else:
            # Check BTC trend and correlation
            btc_df = self._get_indicator_window("BTC-USD", "4h", lookback=60)
            if btc_df is not None and len(btc_df) >= 14 and len(df) >= 14:
                btc_bar = btc_df.iloc[-1]
                btc_sma20 = btc_bar.get("sma_20")
                btc_price = btc_bar.get("close")

                btc_in_downtrend = (btc_price is not None and btc_sma20 is not None
                                    and btc_price < btc_sma20)

                if btc_in_downtrend:
                    # Rolling correlation over config window
                    window = self.config["correlation_window_days"]
                    # Use available close data — min of both series
                    n = min(len(df), len(btc_df), window * 6)  # ~6 4h bars per day
                    if n >= 14:
                        sym_returns = df["close"].iloc[-n:].pct_change().dropna()
                        btc_returns = btc_df["close"].iloc[-n:].pct_change().dropna()
                        min_len = min(len(sym_returns), len(btc_returns))
                        if min_len >= 10:
                            corr = sym_returns.iloc[-min_len:].corr(btc_returns.iloc[-min_len:])
                            if corr >= self.config["correlation_threshold"]:
                                strength = 50 + min((corr - 0.8) / 0.2, 1.0) * 15
                                reason = (f"Correlation contagion: BTC in downtrend, "
                                          f"corr={corr:.2f} >= {self.config['correlation_threshold']}, "
                                          f"P&L={pnl_frac:.2%}")
                                logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")
                                return {
                                    "symbol": symbol,
                                    "action": "SELL",
                                    "strength": round(strength, 1),
                                    "reason": reason,
                                    "exit_type": "correlation_contagion",
                                    "_strategy_name": "exit_overlay",
                                    "_timeframe": "4h",
                                }
                            else:
                                logger.debug(f"[ExitOverlay/{symbol}] correlation: BTC corr={corr:.2f} "
                                            f"< {self.config['correlation_threshold']} — hold")

        # Cluster loss check: if 3+ positions losing, exit the worst
        cluster_count = self.config["cluster_loss_count"]
        losing = []
        for sym, pos_data in portfolio_positions.items():
            pos_info = self._get_position_entry(sym)
            if pos_info:
                p = pos_info.get("pnl_percent", 0) or 0
                p_frac = p / 100.0 if abs(p) > 1 else p
                if p_frac < min_loss:
                    losing.append((sym, p_frac))

        if len(losing) >= cluster_count:
            # Find the worst loser
            losing.sort(key=lambda x: x[1])
            worst_sym, worst_pnl = losing[0]
            if worst_sym == symbol:
                strength = 55
                reason = (f"Correlation cluster: {len(losing)} positions losing, "
                          f"this is worst at {worst_pnl:.2%}")
                logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")
                return {
                    "symbol": symbol,
                    "action": "SELL",
                    "strength": round(strength, 1),
                    "reason": reason,
                    "exit_type": "correlation_contagion",
                    "_strategy_name": "exit_overlay",
                    "_timeframe": "4h",
                }

        logger.debug(f"[ExitOverlay/{symbol}] correlation: no trigger "
                    f"(losing={len(losing)}/{cluster_count} cluster)")
        return None

    def check_time_deterioration_exit(self, symbol: str,
                                      position: dict) -> Optional[dict]:
        """
        Exit positions held too long without profit.

        If held > max_hold_days AND P&L < 0, exit.
        """
        max_days = self.config["max_hold_days"]
        max_hours = max_days * 24

        hours = self._hours_held(position.get("entry_time", ""))
        if hours < max_hours:
            logger.debug(f"[ExitOverlay/{symbol}] time: held {hours/24:.1f}d < {max_days}d max — hold")
            return None

        pnl = position.get("pnl_percent", 0) or 0
        pnl_frac = pnl / 100.0  # Convert percentage to fraction

        min_loss = self.config["min_loss_pct_to_exit"]
        if pnl_frac > min_loss:
            logger.debug(f"[ExitOverlay/{symbol}] time: P&L={pnl_frac:.2%} above min loss — hold")
            return None

        # Strength 50-60 based on how long overdue
        overage = (hours - max_hours) / (max_hours * 0.5)  # 0-1 scale over 50% extra time
        strength = 50 + min(overage, 1.0) * 10

        reason = (f"Time deterioration: held {hours/24:.1f}d > {max_days}d max, "
                  f"P&L={pnl_frac:.2%}")
        logger.info(f"[ExitOverlay/{symbol}] {reason} → SELL strength={strength:.0f}")

        return {
            "symbol": symbol,
            "action": "SELL",
            "strength": round(strength, 1),
            "reason": reason,
            "exit_type": "time_deterioration",
            "_strategy_name": "exit_overlay",
            "_timeframe": "4h",
        }

    def check_all_exits(self, portfolio_positions: dict,
                        db_path: str = None) -> List[dict]:
        """
        Run all exit checks on all held positions.

        Returns a list of SELL signals. If multiple exit conditions fire
        for the same symbol, uses the STRONGEST one.
        """
        if db_path:
            self.db_path = db_path

        exit_signals = []

        for symbol, pos_data in portfolio_positions.items():
            qty = pos_data.get("quantity", 0)
            if qty <= 0:
                continue

            # Get full position info from DB
            position = self._get_position_entry(symbol) or {}
            # Merge with pos_data for avg_price fallback
            if not position.get("entry_price") and pos_data.get("avg_price"):
                position["entry_price"] = pos_data["avg_price"]

            # Load indicator window
            df = self._get_indicator_window(symbol, "4h", lookback=60)

            # Calculate live P&L if not in DB
            if "pnl_percent" not in position and df is not None and len(df) > 0:
                current_price = df.iloc[-1]["close"]
                entry_price = position.get("entry_price", 0)
                if entry_price > 0:
                    position["pnl_percent"] = self._pnl_from_price(entry_price, current_price)

            candidates = []

            # Run each exit check
            if df is not None:
                sig = self.check_momentum_exit(symbol, df, position)
                if sig:
                    candidates.append(sig)

                sig = self.check_trend_break_exit(symbol, df, position)
                if sig:
                    candidates.append(sig)

                sig = self.check_volatility_exit(symbol, df, position)
                if sig:
                    candidates.append(sig)

                sig = self.check_correlation_exit(symbol, df, position, portfolio_positions)
                if sig:
                    candidates.append(sig)
            else:
                logger.warning(f"[ExitOverlay/{symbol}] No indicator data — skipping chart-based checks")

            sig = self.check_time_deterioration_exit(symbol, position)
            if sig:
                candidates.append(sig)

            # Keep the strongest signal per symbol
            if candidates:
                best = max(candidates, key=lambda s: s["strength"])
                exit_signals.append(best)
                logger.info(f"[ExitOverlay/{symbol}] Best exit: {best['exit_type']} "
                           f"strength={best['strength']:.0f} "
                           f"(from {len(candidates)} triggers)")
            else:
                logger.info(f"[ExitOverlay/{symbol}] All checks: HOLD")

        if exit_signals:
            logger.info(f"[ExitOverlay] Total: {len(exit_signals)} exit signals "
                       f"from {len(portfolio_positions)} held positions")

        return exit_signals
