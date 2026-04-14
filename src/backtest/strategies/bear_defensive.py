"""
BearDefensive — Tight mean-reversion strategy for bear market regimes.

Designed to operate defensively when the regime detector classifies the
market as 'risk-off' or 'bear'. Compared to BollingerReversion:

  - Requires deeper oversold conditions (BB position < 0.05, RSI < 25)
  - Exits quickly at a much lower target (BB position > 0.30, RSI > 40)
  - Signals weaker bars (max weight 0.60 vs 1.0 for other strategies)
  - Max recommended position: 5% of portfolio (enforced by caller)

The strategy favours capital preservation over maximising returns.
It will generate fewer signals than other strategies — that is intentional.
Silence means "stay in cash", which is the correct bear-market posture.

Walk-forward validation requirements before deployment:
  - OOS Sharpe > 0 (positive expectancy in bear periods)
  - Min 10 trades across at least 2 bear regimes
  - Breakeven fee > 0.1% (robust to standard crypto fees)
"""

import pandas as pd

from . import Strategy, register_strategy


@register_strategy
class BearDefensiveStrategy(Strategy):
    """
    Tight Bollinger + RSI mean-reversion for bear/risk-off regimes.

    Entry  : BB position < entry_bb AND RSI < entry_rsi
    Exit   : BB position > exit_bb  OR  RSI > exit_rsi
    Max pos: 5% of portfolio (signals weight capped at 0.60)
    """

    name = "BearDefensive"

    def __init__(
        self,
        entry_bb: float = 0.05,  # enter when price in bottom 5% of BB band
        exit_bb: float = 0.30,  # exit when price reaches 30% of BB band
        entry_rsi: float = 25.0,  # require deep oversold (vs 30 for BollingerReversion)
        exit_rsi: float = 40.0,  # exit early (vs 50+ for other strategies)
        min_adx: float = 0.0,  # no ADX requirement — works in any trend strength
        max_adx: float = 60.0,  # skip if extreme trend (> 60 = crash, avoid catching)
        max_weight: float = 0.60,  # cap signal weight (conservative position sizing)
    ):
        self.entry_bb = entry_bb
        self.exit_bb = exit_bb
        self.entry_rsi = entry_rsi
        self.exit_rsi = exit_rsi
        self.min_adx = min_adx
        self.max_adx = max_adx
        self.max_weight = max_weight

    def generate_signals(self, df: pd.DataFrame, portfolio: dict) -> list:
        if len(df) < 5:
            return []

        bar = df.iloc[-1]
        bb_upper = bar.get("bb_upper")
        bb_lower = bar.get("bb_lower")
        bb_middle = bar.get("bb_middle")
        rsi = bar.get("rsi_14")
        adx = bar.get("adx_14")
        close = bar.get("close")

        # Need BB and RSI
        if any(
            v is None or (hasattr(v, "__float__") and pd.isna(v))
            for v in [bb_upper, bb_lower, bb_middle, rsi, close]
        ):
            return []

        # Skip on missing or extreme ADX (possible flash crash — avoid catching)
        if adx is not None and not pd.isna(adx):
            if adx > self.max_adx:
                return []

        # BB position: 0.0 = at lower band, 1.0 = at upper band
        bb_range = float(bb_upper) - float(bb_lower)
        if bb_range <= 0:
            return []
        bb_pos = (float(close) - float(bb_lower)) / bb_range

        rsi_f = float(rsi)

        # ── Check current position for exit signals ───────────────────
        held = portfolio.get("positions", {})
        if held:
            # If we hold this asset, look for exit conditions
            # (The engine passes portfolio with positions dict)
            if bb_pos > self.exit_bb or rsi_f > self.exit_rsi:
                exit_strength = 0.0
                reasons = []
                if bb_pos > self.exit_bb:
                    exit_strength += (bb_pos - self.exit_bb) * 2.0
                    reasons.append(f"BB_pos={bb_pos:.2f}>{self.exit_bb}")
                if rsi_f > self.exit_rsi:
                    exit_strength += (rsi_f - self.exit_rsi) / 10.0
                    reasons.append(f"RSI={rsi_f:.1f}>{self.exit_rsi:.0f}")
                weight = min(max(exit_strength / 3.0, 0.3), 1.0)
                return [
                    {
                        "action": "SELL",
                        "weight": weight,
                        "reason": f"BearDefensive exit: {' | '.join(reasons)}",
                    }
                ]

        # ── Entry signal: deep oversold only ─────────────────────────
        if bb_pos < self.entry_bb and rsi_f < self.entry_rsi:
            # Strength proportional to how deep the oversold reading is
            bb_strength = (self.entry_bb - bb_pos) / self.entry_bb  # 0-1
            rsi_strength = (self.entry_rsi - rsi_f) / self.entry_rsi  # 0-1
            raw_weight = bb_strength * 0.6 + rsi_strength * 0.4
            weight = min(raw_weight, self.max_weight)
            return [
                {
                    "action": "BUY",
                    "weight": weight,
                    "reason": (
                        f"BearDefensive entry: BB_pos={bb_pos:.3f}<{self.entry_bb} "
                        f"RSI={rsi_f:.1f}<{self.entry_rsi:.0f}"
                    ),
                }
            ]

        return []

    def get_parameters(self) -> dict:
        return {
            "entry_bb": self.entry_bb,
            "exit_bb": self.exit_bb,
            "entry_rsi": self.entry_rsi,
            "exit_rsi": self.exit_rsi,
            "max_adx": self.max_adx,
            "max_weight": self.max_weight,
        }
