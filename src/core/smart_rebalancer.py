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
    DB_PATH,
    PAPER_STATE_FILE,
    PAPER_TRANSACTION_COST,
    PAPER_MIN_TRADE_USD,
    MAX_POSITION_PCT,
    MAX_SECTOR_PCT,
    TELEGRAM_CHAT_ID,
    get_sector,
    SYMBOL_TIERS,
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
        gatekeeper=None,
    ):
        self.db_path = db_path
        self.drift_threshold = drift_threshold
        self.min_trade_usd = min_trade_usd
        self.max_positions = max_positions
        self.min_positions = min_positions
        self.gatekeeper = gatekeeper  # optional LLMGatekeeper instance

    # ── Category cap configuration ────────────────────────────────────────
    # Combined cap per category and minimum floor for large-cap.
    CATEGORY_CAPS: Dict[str, float] = {
        "meme": 0.10,  # meme coins ≤ 10% combined
        "small": 0.15,  # small-cap ≤ 15% combined
    }
    LARGE_CAP_FLOOR: float = 0.40  # BTC+ETH+BNB+SOL ≥ 40% combined
    LARGE_CAP_SYMBOLS = {"BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD"}

    # Quality gate: exclude symbols below this quality score from the
    # rebalancer's buy universe.
    QUALITY_GATE_MIN_SCORE: float = 35.0  # meme (30) excluded; small (50) included

    def compute_target_weights(
        self, scores: Dict[str, float], top_n: int = None
    ) -> Dict[str, float]:
        """
        Compute score-weighted target allocations with three constraint layers:
          1. Per-position cap (MAX_POSITION_PCT)
          2. Per-sector cap (MAX_SECTOR_PCT)
          3. Category caps (meme ≤ 10%, small ≤ 15%, large-cap ≥ 40%)
             with quality gate (exclude meme-grade assets from buy universe)
        """
        if top_n is None:
            top_n = self.max_positions

        # ── Quality gate ─────────────────────────────────────────────
        try:
            from config import TIER_QUALITY_SCORES
        except ImportError:
            TIER_QUALITY_SCORES = {"large": 90, "mid": 70, "small": 50, "meme": 30}

        def _tier_quality(sym: str) -> float:
            tier = SYMBOL_TIERS.get(sym, "small")
            return float(TIER_QUALITY_SCORES.get(tier, 50))

        eligible = {
            sym: sc
            for sym, sc in scores.items()
            if _tier_quality(sym) >= self.QUALITY_GATE_MIN_SCORE
        }

        if len(eligible) < self.min_positions:
            logger.warning(
                f"Quality gate left only {len(eligible)} symbols "
                f"(< min {self.min_positions}) — using all symbols"
            )
            eligible = scores

        # Sort by score descending, take top N
        ranked = sorted(eligible.items(), key=lambda x: x[1], reverse=True)[:top_n]

        if not ranked:
            return {}

        # Score-weighted allocation
        total_score = sum(s for _, s in ranked)
        if total_score <= 0:
            weight = 1.0 / len(ranked)
            return {sym: weight for sym, _ in ranked}

        raw_weights = {sym: score / total_score for sym, score in ranked}

        # ── Layer 1: Per-position cap ─────────────────────────────────
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

        # ── Layer 2: Per-sector cap ───────────────────────────────────
        sector_weights: Dict[str, float] = {}
        for sym, w in capped.items():
            sector = get_sector(sym)
            sector_weights[sector] = sector_weights.get(sector, 0) + w

        for sector, sw in sector_weights.items():
            if sw > MAX_SECTOR_PCT:
                scale = MAX_SECTOR_PCT / sw
                for sym in capped:
                    if get_sector(sym) == sector:
                        capped[sym] *= scale

        # Normalize after layers 1+2 before applying category constraints
        total = sum(capped.values())
        if total > 0:
            capped = {sym: w / total for sym, w in capped.items()}

        # ── Layer 3: Category caps (applied on normalized weights) ────
        # Freed weight is redistributed to non-capped symbols so the
        # total stays at 1.0 without a second normalization.
        for category, cap in self.CATEGORY_CAPS.items():
            cat_syms = [sym for sym in capped if SYMBOL_TIERS.get(sym) == category]
            cat_total = sum(capped[sym] for sym in cat_syms)
            if cat_total > cap and cat_total > 0:
                freed = cat_total - cap
                scale = cap / cat_total
                for sym in cat_syms:
                    capped[sym] *= scale
                # Redistribute freed weight proportionally to non-category symbols
                non_cat = [sym for sym in capped if sym not in cat_syms]
                non_cat_total = sum(capped[sym] for sym in non_cat)
                if non_cat_total > 0:
                    for sym in non_cat:
                        capped[sym] += freed * (capped[sym] / non_cat_total)
                logger.info(f"Category cap: {category} {cat_total:.1%} → {cap:.1%}")

        # Enforce large-cap floor (BTC+ETH+BNB+SOL ≥ 40%)
        lc_syms = [sym for sym in capped if sym in self.LARGE_CAP_SYMBOLS]
        lc_total = sum(capped.get(sym, 0) for sym in lc_syms)
        if lc_syms and lc_total < self.LARGE_CAP_FLOOR:
            deficit = self.LARGE_CAP_FLOOR - lc_total
            non_lc = {
                sym: w for sym, w in capped.items() if sym not in self.LARGE_CAP_SYMBOLS
            }
            non_lc_total = sum(non_lc.values())
            if non_lc_total > deficit:
                # Take deficit from non-large-cap proportionally
                for sym in non_lc:
                    reduction = capped[sym] / non_lc_total * deficit
                    capped[sym] = max(0, capped[sym] - reduction)
                # Give deficit to large-cap proportionally
                for sym in lc_syms:
                    capped[sym] += deficit * (capped[sym] / max(lc_total, 1e-9))
                logger.info(
                    f"Large-cap floor: {lc_total:.1%} → {self.LARGE_CAP_FLOOR:.1%}"
                )

        return capped

        return capped

    def calculate_drift(
        self, current_weights: Dict[str, float], target_weights: Dict[str, float]
    ) -> Dict[str, float]:
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

    def should_rebalance(
        self, current_weights: Dict[str, float], target_weights: Dict[str, float]
    ) -> Tuple[bool, float, str]:
        """
        Determine if rebalancing is warranted.
        Returns (should_rebalance, max_drift, reason).
        """
        drift = self.calculate_drift(current_weights, target_weights)
        md = self.max_drift(drift)

        # New symbols to add
        new_symbols = [
            s
            for s in target_weights
            if s not in current_weights and target_weights[s] > 0.02
        ]
        # Symbols to remove
        old_symbols = [
            s
            for s in current_weights
            if s not in target_weights and current_weights[s] > 0.02
        ]

        if md >= self.drift_threshold:
            return (
                True,
                md,
                f"Max drift {md:.1%} exceeds threshold {self.drift_threshold:.1%}",
            )
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
                sells.append(
                    {
                        "symbol": sym,
                        "side": "SELL",
                        "amount_usd": abs(diff),
                        "reason": "rebalance",
                    }
                )
            else:
                # Buy
                buys.append(
                    {
                        "symbol": sym,
                        "side": "BUY",
                        "amount_usd": diff,
                        "reason": "rebalance",
                    }
                )

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
        trades = self.compute_trades(
            current_positions, target_weights, portfolio_value, portfolio.capital
        )

        # Set sticky decision context for all rebalance trades (survives batch)
        portfolio._sticky_decision_context = json.dumps(
            {
                "signal_type": "smart_rebalance",
                "trader": "smart_rebalancer",
                "reason": reason,
                "max_drift": round(max_d, 4),
                "drift_threshold": self.drift_threshold,
                "target_weights": {k: round(v, 4) for k, v in target_weights.items()},
                "current_weights": {k: round(v, 4) for k, v in current_weights.items()},
            }
        )

        # ── Gatekeeper: validate BUY rebalance trades via LLM ──────────────
        # Converts buy trades to signal-like dicts, validates, then maps back.
        approved_buys: set = set()
        buy_trades = [t for t in trades if t["side"] == "BUY"]
        if self.gatekeeper and buy_trades:
            _pf_value = portfolio.get_portfolio_value()
            _pf_state = {
                "value": _pf_value,
                "cash": portfolio.capital,
                "positions": {
                    sym: {
                        "value": round(
                            portfolio.get_current_price(sym) * pos["quantity"], 2
                        ),
                        "pnl_pct": 0,
                    }
                    for sym, pos in portfolio.positions.items()
                },
            }
            _signals = [
                {
                    "symbol": t["symbol"],
                    "action": "BUY",
                    "strength": 60,  # rebalance trades use a neutral baseline strength
                    "reason": f"rebalance drift correction (target={target_weights.get(t['symbol'], 0):.1%})",
                    "_strategy_name": "smart_rebalancer",
                    "_timeframe": "1d",
                }
                for t in buy_trades
            ]
            try:
                validated = self.gatekeeper.validate_signals(
                    _signals, _pf_state, "rebalance"
                )
                approved_buys = {s["symbol"] for s in validated}
                gk_rejected = len(buy_trades) - len(approved_buys)
                if gk_rejected:
                    logger.info(
                        f"Rebalancer gatekeeper: {gk_rejected} BUY trade(s) rejected"
                    )
            except Exception as e:
                logger.warning(f"Rebalancer gatekeeper failed — proceeding: {e}")
                approved_buys = {t["symbol"] for t in buy_trades}
        else:
            # No gatekeeper: all BUY trades approved
            approved_buys = {t["symbol"] for t in buy_trades}

        executed = []
        for trade in trades:
            sym = trade["symbol"]

            # ── Transaction cost gate: skip if fee > 50% of drift benefit ────
            # This prevents small rebalance trades where fees exceed the value
            # of correcting the drift. Applies to both BUY and SELL.
            estimated_fee = trade.get(
                "estimated_fee", trade["amount_usd"] * PAPER_TRANSACTION_COST
            )
            drift_benefit = (
                abs(target_weights.get(sym, 0) - current_weights.get(sym, 0))
                * portfolio_value
            )
            if drift_benefit > 0 and estimated_fee > drift_benefit * 0.50:
                logger.info(
                    f"Skip rebalance {trade['side']} {sym}: "
                    f"fee ${estimated_fee:.4f} > 50% of drift benefit ${drift_benefit:.4f}"
                )
                continue

            if trade["side"] == "SELL":
                if sym in portfolio.positions:
                    price = portfolio.get_current_price(sym)
                    if price > 0:
                        sell_qty = min(
                            trade["amount_usd"] / price,
                            portfolio.positions[sym]["quantity"],
                        )
                        if portfolio.execute_sell(sym, sell_qty):
                            executed.append(trade)
            elif trade["side"] == "BUY":
                if sym not in approved_buys:
                    logger.info(f"Rebalance BUY {sym} skipped by gatekeeper")
                    continue
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

        logger.info(
            f"Rebalanced: {len(executed)}/{len(trades)} trades, fees=${total_fees:.4f}"
        )
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
            lines.append(
                f"Rebalanced: {result['trades_completed']}/{result['trades_planned']} trades"
            )
            lines.append(f"Reason: {result.get('reason', 'N/A')}")
            lines.append(f"Fees: ${result.get('total_fees', 0):.4f}")
            lines.append(
                f"Value: ${result.get('portfolio_value', 0):.2f} -> ${result.get('post_value', 0):.2f}"
            )
            lines.append("")
            lines.append("**Target Weights:**")
            for sym, w in sorted(
                result.get("target_weights", {}).items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"  {sym}: {w:.1%}")
        else:
            lines.append(f"No rebalance needed")
            lines.append(
                f"Max drift: {result.get('max_drift', 0):.1%} (threshold: {result.get('drift_threshold', 0):.1%})"
            )

        return "\n".join(lines)


if __name__ == "__main__":
    print("Smart Rebalancer — use via trading_agent.py")
