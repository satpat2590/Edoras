#!/usr/bin/env python3
"""
Smart Rebalancer — drift-based, cost-aware portfolio rebalancing.
Only rebalances when portfolio drift exceeds threshold.
Uses score-weighted allocations instead of rigid equal-weight.
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DB_PATH, PAPER_STATE_FILE, PAPER_TRANSACTION_COST,
    PAPER_MIN_TRADE_USD, MAX_POSITION_PCT, MAX_SECTOR_PCT,
    TELEGRAM_CHAT_ID, get_sector,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class SmartRebalancer:
    """Drift-based, cost-aware portfolio rebalancer."""

    def __init__(
        self,
        db_path: str = DB_PATH,
        drift_threshold: float = 0.05,
        min_trade_usd: float = PAPER_MIN_TRADE_USD,
        max_positions: int = 7,
        min_positions: int = 4,
    ):
        self.db_path = db_path
        self.drift_threshold = drift_threshold
        self.min_trade_usd = min_trade_usd
        self.max_positions = max_positions
        self.min_positions = min_positions

    def compute_target_weights(self, scores: Dict[str, float], top_n: int = None) -> Dict[str, float]:
        """
        Compute score-weighted target allocations.
        Higher-scoring assets get proportionally more weight,
        capped at MAX_POSITION_PCT per position and MAX_SECTOR_PCT per sector.
        """
        if top_n is None:
            top_n = self.max_positions

        # Sort by score descending, take top N
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]

        if not ranked:
            return {}

        # Score-weighted allocation
        total_score = sum(s for _, s in ranked)
        if total_score <= 0:
            # Fallback to equal weight
            weight = 1.0 / len(ranked)
            return {sym: weight for sym, _ in ranked}

        raw_weights = {sym: score / total_score for sym, score in ranked}

        # Apply position cap
        capped = {}
        excess = 0.0
        uncapped_count = 0
        for sym, w in raw_weights.items():
            if w > MAX_POSITION_PCT:
                capped[sym] = MAX_POSITION_PCT
                excess += w - MAX_POSITION_PCT
            else:
                capped[sym] = w
                uncapped_count += 1

        # Redistribute excess proportionally to uncapped positions
        if excess > 0 and uncapped_count > 0:
            uncapped_total = sum(w for sym, w in capped.items() if w < MAX_POSITION_PCT)
            if uncapped_total > 0:
                for sym in capped:
                    if capped[sym] < MAX_POSITION_PCT:
                        capped[sym] += excess * (capped[sym] / uncapped_total)

        # Apply sector cap
        sector_weights: Dict[str, float] = {}
        for sym, w in capped.items():
            sector = get_sector(sym)
            sector_weights[sector] = sector_weights.get(sector, 0) + w

        for sector, sw in sector_weights.items():
            if sw > MAX_SECTOR_PCT:
                # Scale down all positions in this sector
                scale = MAX_SECTOR_PCT / sw
                for sym in capped:
                    if get_sector(sym) == sector:
                        capped[sym] *= scale

        # Normalize to sum to 1.0
        total = sum(capped.values())
        if total > 0:
            capped = {sym: w / total for sym, w in capped.items()}

        return capped

    def calculate_drift(self, current_weights: Dict[str, float], target_weights: Dict[str, float]) -> Dict[str, float]:
        """Calculate per-position drift from target."""
        all_symbols = set(list(current_weights.keys()) + list(target_weights.keys()))
        drift = {}
        for sym in all_symbols:
            current = current_weights.get(sym, 0.0)
            target = target_weights.get(sym, 0.0)
            drift[sym] = target - current
        return drift

    def max_drift(self, drift: Dict[str, float]) -> float:
        """Maximum absolute drift across all positions."""
        if not drift:
            return 0.0
        return max(abs(d) for d in drift.values())

    def should_rebalance(self, current_weights: Dict[str, float], target_weights: Dict[str, float]) -> Tuple[bool, float, str]:
        """
        Determine if rebalancing is warranted.
        Returns (should_rebalance, max_drift, reason).
        """
        drift = self.calculate_drift(current_weights, target_weights)
        md = self.max_drift(drift)

        # New symbols to add
        new_symbols = [s for s in target_weights if s not in current_weights and target_weights[s] > 0.02]
        # Symbols to remove
        old_symbols = [s for s in current_weights if s not in target_weights and current_weights[s] > 0.02]

        if md >= self.drift_threshold:
            return True, md, f"Max drift {md:.1%} exceeds threshold {self.drift_threshold:.1%}"
        if new_symbols:
            return True, md, f"New symbols to add: {', '.join(new_symbols)}"
        if old_symbols:
            return True, md, f"Symbols to remove: {', '.join(old_symbols)}"

        return False, md, f"Max drift {md:.1%} within threshold"

    def compute_trades(
        self,
        current_positions: Dict[str, float],
        target_weights: Dict[str, float],
        portfolio_value: float,
        cash: float,
    ) -> List[Dict]:
        """
        Compute the list of trades needed to reach target allocation.
        Returns sell trades first (to free cash), then buy trades.
        """
        trades = []
        sells = []
        buys = []

        for sym in set(list(current_positions.keys()) + list(target_weights.keys())):
            current_value = current_positions.get(sym, 0.0)
            target_value = portfolio_value * target_weights.get(sym, 0.0)
            diff = target_value - current_value

            if abs(diff) < self.min_trade_usd:
                continue

            if diff < 0:
                # Sell
                sells.append({"symbol": sym, "side": "SELL", "amount_usd": abs(diff), "reason": "rebalance"})
            else:
                # Buy
                buys.append({"symbol": sym, "side": "BUY", "amount_usd": diff, "reason": "rebalance"})

        # Sells first to free cash
        trades.extend(sells)
        trades.extend(buys)

        # Estimate total fees
        total_volume = sum(t["amount_usd"] for t in trades)
        estimated_fees = total_volume * PAPER_TRANSACTION_COST

        for t in trades:
            t["estimated_fee"] = t["amount_usd"] * PAPER_TRANSACTION_COST

        return trades

    def execute_rebalance(self, portfolio, scores: Dict[str, float]) -> Dict:
        """
        Full rebalance flow: compute targets, check drift, execute if needed.

        Args:
            portfolio: PaperTradingPortfolio instance
            scores: {symbol: total_score} from the scoring model

        Returns: dict with rebalance results
        """
        portfolio_value = portfolio.get_portfolio_value()
        if portfolio_value <= 0:
            return {"rebalanced": False, "reason": "Portfolio value is zero"}

        # Current weights
        current_positions = {}
        current_weights = {}
        for sym, pos in portfolio.positions.items():
            price = portfolio.get_current_price(sym)
            value = price * pos["quantity"]
            current_positions[sym] = value
            current_weights[sym] = value / portfolio_value

        # Target weights (score-weighted)
        target_weights = self.compute_target_weights(scores)

        if not target_weights:
            return {"rebalanced": False, "reason": "No valid target weights"}

        # Check drift
        should, max_d, reason = self.should_rebalance(current_weights, target_weights)

        result = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": portfolio_value,
            "cash": portfolio.capital,
            "current_weights": {k: round(v, 4) for k, v in current_weights.items()},
            "target_weights": {k: round(v, 4) for k, v in target_weights.items()},
            "max_drift": round(max_d, 4),
            "drift_threshold": self.drift_threshold,
        }

        if not should:
            result["rebalanced"] = False
            result["reason"] = reason
            logger.info(f"Rebalance not needed: {reason}")
            return result

        # Compute and execute trades
        trades = self.compute_trades(current_positions, target_weights, portfolio_value, portfolio.capital)

        # Set sticky decision context for all rebalance trades (survives batch)
        portfolio._sticky_decision_context = json.dumps({
            "signal_type": "smart_rebalance",
            "trader": "smart_rebalancer",
            "reason": reason,
            "max_drift": round(max_d, 4),
            "drift_threshold": self.drift_threshold,
            "target_weights": {k: round(v, 4) for k, v in target_weights.items()},
            "current_weights": {k: round(v, 4) for k, v in current_weights.items()},
        })

        executed = []
        for trade in trades:
            sym = trade["symbol"]
            if trade["side"] == "SELL":
                if sym in portfolio.positions:
                    price = portfolio.get_current_price(sym)
                    if price > 0:
                        sell_qty = min(trade["amount_usd"] / price, portfolio.positions[sym]["quantity"])
                        if portfolio.execute_sell(sym, sell_qty):
                            executed.append(trade)
            elif trade["side"] == "BUY":
                buy_amount = min(trade["amount_usd"], portfolio.capital * 0.95)
                if buy_amount >= self.min_trade_usd:
                    if portfolio.execute_buy(sym, buy_amount):
                        executed.append(trade)

        result["rebalanced"] = len(executed) > 0
        result["reason"] = reason
        result["trades_executed"] = executed
        result["trades_planned"] = len(trades)
        result["trades_completed"] = len(executed)

        # Clear sticky context after batch
        portfolio._sticky_decision_context = None

        total_fees = sum(t.get("estimated_fee", 0) for t in executed)
        result["total_fees"] = round(total_fees, 4)
        result["post_value"] = portfolio.get_portfolio_value()

        # Update paper state
        self._save_state(target_weights, result)

        logger.info(f"Rebalanced: {len(executed)}/{len(trades)} trades, fees=${total_fees:.4f}")
        return result

    def _save_state(self, target_weights: Dict[str, float], result: Dict):
        """Save rebalancing state for tracking."""
        state = {
            "last_rebalanced": datetime.now().isoformat(),
            "symbols": list(target_weights.keys()),
            "target_allocation": target_weights,
            "portfolio_value": result.get("post_value", 0),
            "method": "smart_drift_based",
            "last_drift": result.get("max_drift", 0),
        }
        try:
            with open(PAPER_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save state: {e}")

    def format_report(self, result: Dict) -> str:
        """Format rebalance result as Telegram message."""
        lines = []
        lines.append("**Smart Rebalancer Report**")
        lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if result.get("rebalanced"):
            lines.append(f"Rebalanced: {result['trades_completed']}/{result['trades_planned']} trades")
            lines.append(f"Reason: {result.get('reason', 'N/A')}")
            lines.append(f"Fees: ${result.get('total_fees', 0):.4f}")
            lines.append(f"Value: ${result.get('portfolio_value', 0):.2f} -> ${result.get('post_value', 0):.2f}")
            lines.append("")
            lines.append("**Target Weights:**")
            for sym, w in sorted(result.get("target_weights", {}).items(), key=lambda x: x[1], reverse=True):
                lines.append(f"  {sym}: {w:.1%}")
        else:
            lines.append(f"No rebalance needed")
            lines.append(f"Max drift: {result.get('max_drift', 0):.1%} (threshold: {result.get('drift_threshold', 0):.1%})")

        return "\n".join(lines)


if __name__ == "__main__":
    print("Smart Rebalancer — use via trading_agent.py")
