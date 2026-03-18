#!/usr/bin/env python3
"""
Central risk management engine.
Checks position-level stops/take-profits and portfolio-level limits.
Persists state (high watermarks, partial exits) to disk.
"""

import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys_path_hack = True  # ensure local imports work
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH,
    RISK_STATE_FILE,
    STOP_LOSS_PCT,
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_PCT,
    TAKE_PROFIT_LEVELS,
    MAX_PORTFOLIO_DRAWDOWN,
    MAX_POSITION_PCT,
    MAX_SECTOR_PCT,
    CRYPTO_CATEGORIES,
    EQUITY_SECTORS,
    get_sector,
)
from exit_signals import ExitSignal, CircuitBreaker, RiskViolation

logger = logging.getLogger(__name__)


class RiskManager:
    """Manages stop-losses, take-profits, trailing stops, and portfolio risk limits."""

    def __init__(self, db_path: str = DB_PATH, state_file: str = RISK_STATE_FILE):
        self.db_path = db_path
        self.state_file = state_file

        # Per-position tracking
        self.entry_prices: Dict[str, float] = {}        # symbol -> original entry price
        self.high_watermarks: Dict[str, float] = {}      # symbol -> highest price since entry
        self.partial_exits: Dict[str, List[int]] = {}    # symbol -> list of TP level indices hit

        # Portfolio-level tracking
        self.portfolio_peak: float = 0.0
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_event: Optional[CircuitBreaker] = None

        self._load_state()

    # ── State persistence ────────────────────────────────────────────────

    def _load_state(self):
        """Load persisted risk state from disk."""
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            self.entry_prices = data.get("entry_prices", {})
            self.high_watermarks = data.get("high_watermarks", {})
            self.partial_exits = data.get("partial_exits", {})
            self.portfolio_peak = data.get("portfolio_peak", 0.0)
            self.circuit_breaker_active = data.get("circuit_breaker_active", False)
            logger.info(f"Loaded risk state: {len(self.entry_prices)} positions tracked")
        except Exception as e:
            logger.warning(f"Could not load risk state: {e}")

    def save_state(self):
        """Persist risk state to disk."""
        data = {
            "entry_prices": self.entry_prices,
            "high_watermarks": self.high_watermarks,
            "partial_exits": self.partial_exits,
            "portfolio_peak": self.portfolio_peak,
            "circuit_breaker_active": self.circuit_breaker_active,
            "last_updated": datetime.now().isoformat(),
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save risk state: {e}")

    # ── Entry tracking ───────────────────────────────────────────────────

    def record_entry(self, symbol: str, price: float):
        """Record initial entry price for a new position."""
        if symbol not in self.entry_prices:
            self.entry_prices[symbol] = price
            self.high_watermarks[symbol] = price
            self.partial_exits[symbol] = []
            logger.info(f"Recorded entry: {symbol} @ ${price:.4f}")
            self.save_state()

    def remove_position(self, symbol: str):
        """Remove all tracking for a closed position."""
        self.entry_prices.pop(symbol, None)
        self.high_watermarks.pop(symbol, None)
        self.partial_exits.pop(symbol, None)
        self.save_state()

    # ── Price updates ────────────────────────────────────────────────────

    def update_price(self, symbol: str, current_price: float):
        """Update high watermark for a symbol."""
        if symbol in self.high_watermarks:
            if current_price > self.high_watermarks[symbol]:
                self.high_watermarks[symbol] = current_price

    def _get_current_price(self, symbol: str) -> float:
        """Get latest price from DB."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM candlesticks WHERE symbol=? AND timeframe='1h' "
            "ORDER BY timestamp DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0

    def _get_atr(self, symbol: str) -> Optional[float]:
        """Get latest daily ATR from DB."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT atr_14 FROM indicators WHERE symbol=? AND timeframe='1d' "
            "ORDER BY timestamp DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row and row[0] is not None else None

    # ── Position-level checks ────────────────────────────────────────────

    def check_stop_loss(self, symbol: str) -> Optional[ExitSignal]:
        """Check if position should be stopped out (fixed % below entry)."""
        if symbol not in self.entry_prices:
            return None

        entry = self.entry_prices[symbol]
        price = self._get_current_price(symbol)
        if price <= 0:
            return None

        self.update_price(symbol, price)
        stop_price = entry * (1.0 - STOP_LOSS_PCT)

        if price <= stop_price:
            return ExitSignal(
                symbol=symbol,
                exit_type="stop_loss",
                quantity_pct=1.0,
                reason=f"Price ${price:.4f} hit stop-loss at ${stop_price:.4f} ({STOP_LOSS_PCT:.0%} below entry ${entry:.4f})",
                current_price=price,
                trigger_price=stop_price,
                urgency="high",
            )
        return None

    def check_trailing_stop(self, symbol: str) -> Optional[ExitSignal]:
        """Check trailing stop: activates after TRAILING_STOP_ACTIVATION gain, trails from peak."""
        if symbol not in self.entry_prices:
            return None

        entry = self.entry_prices[symbol]
        price = self._get_current_price(symbol)
        if price <= 0:
            return None

        self.update_price(symbol, price)
        gain_pct = (price - entry) / entry

        # Only activate trailing stop after sufficient gain
        if gain_pct < TRAILING_STOP_ACTIVATION:
            return None

        peak = self.high_watermarks.get(symbol, price)

        # Use ATR-based trailing if available, else fixed %
        atr = self._get_atr(symbol)
        if atr and atr > 0:
            trail_price = peak - (2.0 * atr)  # 2x ATR trailing
        else:
            trail_price = peak * (1.0 - TRAILING_STOP_PCT)

        # Trailing stop must be above entry (breakeven floor)
        trail_price = max(trail_price, entry * 1.001)

        if price <= trail_price:
            return ExitSignal(
                symbol=symbol,
                exit_type="trailing_stop",
                quantity_pct=1.0,
                reason=f"Price ${price:.4f} hit trailing stop at ${trail_price:.4f} (peak ${peak:.4f})",
                current_price=price,
                trigger_price=trail_price,
            )
        return None

    def check_take_profit(self, symbol: str) -> Optional[ExitSignal]:
        """Check scale-out take profit levels."""
        if symbol not in self.entry_prices:
            return None

        entry = self.entry_prices[symbol]
        price = self._get_current_price(symbol)
        if price <= 0:
            return None

        self.update_price(symbol, price)
        gain_pct = (price - entry) / entry
        already_hit = self.partial_exits.get(symbol, [])

        for i, (threshold, sell_pct) in enumerate(TAKE_PROFIT_LEVELS):
            if i in already_hit:
                continue
            if gain_pct >= threshold:
                # Mark this level as hit
                if symbol not in self.partial_exits:
                    self.partial_exits[symbol] = []
                self.partial_exits[symbol].append(i)
                self.save_state()

                return ExitSignal(
                    symbol=symbol,
                    exit_type="take_profit",
                    quantity_pct=sell_pct,
                    reason=f"Price ${price:.4f} hit TP level {i+1} (+{threshold:.0%}), selling {sell_pct:.0%}",
                    current_price=price,
                    trigger_price=entry * (1 + threshold),
                )
        return None

    # ── Portfolio-level checks ───────────────────────────────────────────

    def check_portfolio_drawdown(self, portfolio_value: float) -> Optional[CircuitBreaker]:
        """Check if portfolio has breached max drawdown from peak."""
        # Update peak
        if portfolio_value > self.portfolio_peak:
            self.portfolio_peak = portfolio_value
            self.save_state()

        if self.portfolio_peak <= 0:
            return None

        drawdown = (self.portfolio_peak - portfolio_value) / self.portfolio_peak

        if drawdown >= MAX_PORTFOLIO_DRAWDOWN and not self.circuit_breaker_active:
            self.circuit_breaker_active = True
            event = CircuitBreaker(
                triggered_at=datetime.now(),
                portfolio_value=portfolio_value,
                peak_value=self.portfolio_peak,
                drawdown_pct=drawdown,
            )
            self.circuit_breaker_event = event
            self.save_state()
            return event

        return None

    def check_position_concentration(self, positions: Dict[str, float], portfolio_value: float) -> List[RiskViolation]:
        """Check that no single position exceeds MAX_POSITION_PCT."""
        violations = []
        if portfolio_value <= 0:
            return violations

        for symbol, pos_value in positions.items():
            pct = pos_value / portfolio_value
            if pct > MAX_POSITION_PCT:
                violations.append(RiskViolation(
                    violation_type="position_concentration",
                    detail=f"{symbol} is {pct:.1%} of portfolio (limit {MAX_POSITION_PCT:.0%})",
                    current_value=pct,
                    limit_value=MAX_POSITION_PCT,
                ))
        return violations

    def check_sector_exposure(self, positions: Dict[str, float], portfolio_value: float) -> List[RiskViolation]:
        """Check that no sector exceeds MAX_SECTOR_PCT."""
        violations = []
        if portfolio_value <= 0:
            return violations

        sector_totals: Dict[str, float] = {}
        for symbol, pos_value in positions.items():
            sector = get_sector(symbol)
            sector_totals[sector] = sector_totals.get(sector, 0) + pos_value

        for sector, total in sector_totals.items():
            pct = total / portfolio_value
            if pct > MAX_SECTOR_PCT:
                violations.append(RiskViolation(
                    violation_type="sector_exposure",
                    detail=f"Sector '{sector}' is {pct:.1%} of portfolio (limit {MAX_SECTOR_PCT:.0%})",
                    current_value=pct,
                    limit_value=MAX_SECTOR_PCT,
                ))
        return violations

    # ── Aggregate check ──────────────────────────────────────────────────

    def run_all_checks(
        self,
        positions: Dict[str, float],
        portfolio_value: float,
    ) -> Tuple[List[ExitSignal], Optional[CircuitBreaker], List[RiskViolation]]:
        """
        Run all risk checks.
        Returns: (exit_signals, circuit_breaker_event, violations)
        """
        exit_signals: List[ExitSignal] = []
        violations: List[RiskViolation] = []

        # 1. Portfolio drawdown
        cb = self.check_portfolio_drawdown(portfolio_value)

        # 2. If circuit breaker active, signal full liquidation
        if self.circuit_breaker_active:
            for symbol in list(self.entry_prices.keys()):
                exit_signals.append(ExitSignal(
                    symbol=symbol,
                    exit_type="stop_loss",
                    quantity_pct=1.0,
                    reason=f"Circuit breaker active — liquidating all positions",
                    current_price=self._get_current_price(symbol),
                    urgency="high",
                ))
            return exit_signals, cb, violations

        # 3. Per-position checks (order matters: stop_loss > trailing > take_profit)
        for symbol in list(self.entry_prices.keys()):
            # Stop loss first
            sig = self.check_stop_loss(symbol)
            if sig:
                exit_signals.append(sig)
                continue  # don't check trailing/TP if stopped out

            # Trailing stop
            sig = self.check_trailing_stop(symbol)
            if sig:
                exit_signals.append(sig)
                continue

            # Take profit (partial exits)
            sig = self.check_take_profit(symbol)
            if sig:
                exit_signals.append(sig)

        # 4. Concentration & sector checks
        violations.extend(self.check_position_concentration(positions, portfolio_value))
        violations.extend(self.check_sector_exposure(positions, portfolio_value))

        return exit_signals, cb, violations

    def reset_circuit_breaker(self):
        """Manually reset the circuit breaker (after review)."""
        self.circuit_breaker_active = False
        self.circuit_breaker_event = None
        self.portfolio_peak = 0.0
        self.save_state()
        logger.info("Circuit breaker reset")

    def format_risk_report(
        self,
        exit_signals: List[ExitSignal],
        cb: Optional[CircuitBreaker],
        violations: List[RiskViolation],
    ) -> str:
        """Format risk check results as a Telegram-friendly message."""
        lines = []
        lines.append("⚠️ **Risk Manager Report**")
        lines.append(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if cb:
            lines.append(f"🚨 {cb.message}")
            lines.append("")

        if exit_signals:
            lines.append("**Exit Signals:**")
            for sig in exit_signals:
                emoji = "🛑" if sig.urgency == "high" else "⚡"
                lines.append(f"{emoji} {sig.label}: {sig.reason}")
            lines.append("")

        if violations:
            lines.append("**Risk Violations:**")
            for v in violations:
                lines.append(f"⚠️ {v.detail}")
            lines.append("")

        if not exit_signals and not cb and not violations:
            lines.append("✅ All risk checks passed")

        return "\n".join(lines)
