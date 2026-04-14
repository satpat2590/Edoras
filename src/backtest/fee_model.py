"""
FeeModel — per-trade cost modeling for backtesting.

Encapsulates transaction fees and slippage so the engine can resolve
costs per asset class automatically (crypto 0.1%, equity 0%, prediction 2%)
instead of using a flat rate for everything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeeModel:
    """Transaction cost model for a single asset or asset class."""

    fee_pct: float = 0.001  # percentage fee (0.001 = 0.1%)
    slippage_bps: float = 0.0  # basis points of slippage per trade
    min_fee_usd: float = 0.0  # minimum fee per trade

    def compute_fee(self, notional: float) -> float:
        """Compute total fee for a given notional trade amount."""
        return max(notional * self.fee_pct, self.min_fee_usd)

    def apply_slippage(self, price: float, side: str) -> float:
        """Apply slippage to execution price. Buys fill higher, sells lower."""
        slip = price * self.slippage_bps / 10_000
        return price + slip if side == "BUY" else price - slip

    def total_cost_pct(self) -> float:
        """Combined fee + slippage as a percentage (for quick comparisons)."""
        return self.fee_pct + self.slippage_bps / 10_000

    @classmethod
    def from_asset_profile(cls, symbol: str) -> FeeModel:
        """Resolve from ASSET_CLASS_PROFILES via get_asset_class_profile()."""
        from config import get_asset_class_profile

        profile = get_asset_class_profile(symbol)
        return cls(
            fee_pct=profile.get("fee_pct", 0.001),
            slippage_bps=0.0,  # slippage set per-backtest, not per-profile
            min_fee_usd=profile.get("min_fee_usd", 0.0),
        )

    @classmethod
    def flat(cls, fee_pct: float, slippage_bps: float = 0.0) -> FeeModel:
        """Legacy flat fee constructor (backward compat with old transaction_cost param)."""
        return cls(fee_pct=fee_pct, slippage_bps=slippage_bps)
