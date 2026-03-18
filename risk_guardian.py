#!/usr/bin/env python3
"""
Risk Guardian — high-frequency defensive trading loop.
Runs every 30 minutes during active hours (7 AM - 11 PM EDT).

Checks all position-level and portfolio-level risk rules.
Executes exits immediately when triggered — no LLM involved.
Logs events for the trading agent's next strategic review.

This is the safety net. Speed > analysis for exits.
"""

import os
import sys
import json
import sqlite3
import logging
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH, TELEGRAM_CHAT_ID, get_sector
from risk_manager import RiskManager
from exit_signals import ExitSignal, CircuitBreaker, RiskViolation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RISK_EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_events.jsonl")


class RiskGuardian:
    """
    High-frequency risk monitoring and automatic exit execution.
    No LLM. Pure rules. Fast.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.risk_manager = RiskManager(db_path=db_path)
        self._portfolio = None

    @property
    def portfolio(self):
        if self._portfolio is None:
            from paper_trading import PaperTradingPortfolio
            self._portfolio = PaperTradingPortfolio(db_path=self.db_path)
            self._portfolio._current_trader_id = 4  # Risk Engine
        return self._portfolio

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

    # ── Core Check ───────────────────────────────────────────────────────

    def run_check(self) -> Dict:
        """
        Run all risk checks and execute any triggered exits.
        Returns summary of what happened.
        """
        logger.info("Risk guardian check starting")
        result = {
            "timestamp": datetime.now().isoformat(),
            "exits_triggered": [],
            "circuit_breaker": False,
            "violations": [],
            "positions_checked": 0,
        }

        # Sync risk manager entries with actual portfolio positions
        self._sync_positions()

        # Build position values map
        positions = {}
        for sym, pos in self.portfolio.positions.items():
            price = self._get_current_price(sym)
            positions[sym] = price * pos["quantity"]
            # Update high watermarks
            self.risk_manager.update_price(sym, price)

        portfolio_value = self.portfolio.get_portfolio_value()
        result["portfolio_value"] = round(portfolio_value, 2)
        result["positions_checked"] = len(positions)

        # Run all risk checks
        exit_signals, cb, violations = self.risk_manager.run_all_checks(positions, portfolio_value)

        # Execute exits
        for sig in exit_signals:
            executed = self._execute_exit(sig)
            if executed:
                result["exits_triggered"].append({
                    "symbol": sig.symbol,
                    "type": sig.exit_type,
                    "quantity_pct": sig.quantity_pct,
                    "reason": sig.reason,
                    "price": sig.current_price,
                })

        # Circuit breaker
        if cb:
            result["circuit_breaker"] = True
            result["circuit_breaker_detail"] = cb.message
            logger.warning(f"CIRCUIT BREAKER: {cb.message}")

        # Violations (informational — don't auto-trade, just log)
        for v in violations:
            result["violations"].append({
                "type": v.violation_type,
                "detail": v.detail,
                "current": v.current_value,
                "limit": v.limit_value,
            })

        # Log events
        if result["exits_triggered"] or result["circuit_breaker"]:
            self._log_events(result)
            self._store_in_market_intel(result)
            self._send_alert(result)

        # Save risk state
        self.risk_manager.save_state()

        exits = len(result["exits_triggered"])
        viols = len(result["violations"])
        if exits > 0 or viols > 0:
            logger.info(f"Risk guardian: {exits} exits, {viols} violations, CB={result['circuit_breaker']}")
        else:
            logger.info(f"Risk guardian: all clear ({result['positions_checked']} positions checked)")

        return result

    def _sync_positions(self):
        """Ensure risk manager tracks all portfolio positions."""
        for sym, pos in self.portfolio.positions.items():
            if sym not in self.risk_manager.entry_prices:
                # Position exists but risk manager doesn't know about it
                # Use avg_price as entry (best approximation)
                self.risk_manager.record_entry(sym, pos["avg_price"])
                logger.info(f"Synced position {sym} to risk manager (entry ${pos['avg_price']:.4f})")

        # Clean up entries for positions that no longer exist
        for sym in list(self.risk_manager.entry_prices.keys()):
            if sym not in self.portfolio.positions:
                self.risk_manager.remove_position(sym)
                logger.info(f"Removed stale risk entry for {sym}")

    def _execute_exit(self, sig: ExitSignal) -> bool:
        """Execute an exit signal on the paper portfolio."""
        sym = sig.symbol
        if sym not in self.portfolio.positions:
            logger.warning(f"Cannot exit {sym} — no position")
            return False

        # Set decision context for the risk exit
        self.portfolio.set_trade_context(sym, signal_type="risk_exit",
                                         exit_reason=sig.exit_type)
        self.portfolio._pending_decision_context = json.dumps({
            "signal_type": "risk_exit",
            "exit_type": sig.exit_type,
            "reason": getattr(sig, 'reason', sig.exit_type),
            "quantity_pct": sig.quantity_pct,
        })

        if sig.quantity_pct >= 1.0:
            # Full exit
            success = self.portfolio.execute_sell_all(sym)
            if success:
                self.risk_manager.remove_position(sym)
                logger.info(f"EXECUTED: Full exit {sym} ({sig.exit_type})")
                return True
        else:
            # Partial exit
            success = self.portfolio.execute_partial_sell(sym, sig.quantity_pct)
            if success:
                logger.info(f"EXECUTED: Partial exit {sym} {sig.quantity_pct:.0%} ({sig.exit_type})")
                return True

        return False

    # ── Logging ──────────────────────────────────────────────────────────

    def _log_events(self, result: Dict):
        """Append risk events to JSONL log file."""
        try:
            entry = {
                "timestamp": result["timestamp"],
                "portfolio_value": result.get("portfolio_value"),
                "exits": result["exits_triggered"],
                "circuit_breaker": result["circuit_breaker"],
                "violations": result["violations"],
            }
            with open(RISK_EVENTS_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Could not log risk events: {e}")

    def _store_in_market_intel(self, result: Dict):
        """Store risk events in vector memory for future analysis."""
        try:
            from market_intelligence import MarketIntelligence
            mi = MarketIntelligence(db_path=self.db_path)

            parts = []
            for exit in result["exits_triggered"]:
                parts.append(f"Risk exit: {exit['type']} on {exit['symbol']} at ${exit.get('price', 0):.2f}. {exit['reason']}")
            if result["circuit_breaker"]:
                parts.append(f"Circuit breaker activated. {result.get('circuit_breaker_detail', '')}")

            if parts:
                content = " ".join(parts)
                mi.store(content, "risk_event", metadata={
                    "exits": result["exits_triggered"],
                    "circuit_breaker": result["circuit_breaker"],
                })
        except Exception as e:
            logger.warning(f"Could not store in market intelligence: {e}")

    def _send_alert(self, result: Dict):
        """Send immediate Telegram alert for risk events."""
        lines = []
        lines.append("**RISK GUARDIAN ALERT**")
        lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if result["circuit_breaker"]:
            lines.append(f"🚨 {result.get('circuit_breaker_detail', 'CIRCUIT BREAKER ACTIVATED')}")
            lines.append("")

        for exit in result["exits_triggered"]:
            emoji = {"stop_loss": "🛑", "trailing_stop": "⚡", "take_profit": "💰"}.get(exit["type"], "⚠️")
            lines.append(f"{emoji} **{exit['type'].upper()}** {exit['symbol']}")
            lines.append(f"   {exit['reason'][:120]}")
            lines.append("")

        for v in result["violations"]:
            lines.append(f"⚠️ {v['detail']}")

        lines.append(f"Portfolio: ${result.get('portfolio_value', 0):.2f}")

        message = "\n".join(lines)
        if len(message) > 3900:
            message = message[:3900] + "\n..."

        try:
            cmd = ["openclaw", "message", "send", "--target", TELEGRAM_CHAT_ID, "--message", message]
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            logger.info("Risk alert sent to Telegram")
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── Recent Events Query ──────────────────────────────────────────────

    @staticmethod
    def get_recent_events(hours: int = 24) -> List[Dict]:
        """Read recent risk events from the log (for trading agent context)."""
        events = []
        if not os.path.exists(RISK_EVENTS_FILE):
            return events

        cutoff = datetime.now().timestamp() - (hours * 3600)
        try:
            with open(RISK_EVENTS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        event_ts = datetime.fromisoformat(event["timestamp"]).timestamp()
                        if event_ts >= cutoff:
                            events.append(event)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            logger.warning(f"Could not read risk events: {e}")

        return events


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Risk Guardian")
    parser.add_argument("--check", action="store_true", help="Run risk check")
    parser.add_argument("--recent", action="store_true", help="Show recent risk events")
    parser.add_argument("--hours", type=int, default=24, help="Hours lookback for recent events")
    args = parser.parse_args()

    if args.recent:
        events = RiskGuardian.get_recent_events(args.hours)
        if events:
            for e in events:
                print(f"[{e['timestamp']}] Exits: {len(e.get('exits', []))} CB: {e.get('circuit_breaker', False)}")
                for ex in e.get("exits", []):
                    print(f"  {ex['type']} {ex['symbol']}: {ex['reason'][:80]}")
        else:
            print("No recent risk events")
    elif args.check:
        guardian = RiskGuardian()
        result = guardian.run_check()
        print(json.dumps(result, indent=2, default=str))
    else:
        # Default: run check
        guardian = RiskGuardian()
        result = guardian.run_check()
        exits = len(result.get("exits_triggered", []))
        if exits:
            print(f"⚠️  {exits} exits executed")
        else:
            print(f"✅ All clear ({result.get('positions_checked', 0)} positions)")


if __name__ == "__main__":
    main()
