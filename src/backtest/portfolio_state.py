"""
Portfolio state tracking for multi-asset backtesting.

PositionState tracks a single symbol's position.
PortfolioState tracks all positions + cash, and converts to the legacy
portfolio dict format for backward-compatible strategy calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PositionState:
    """State of a single position within a portfolio backtest."""

    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    high_watermark: float = 0.0
    partial_exits_hit: List[int] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.quantity > 0

    def reset(self) -> None:
        """Clear position after full exit."""
        self.quantity = 0.0
        self.entry_price = 0.0
        self.high_watermark = 0.0
        self.partial_exits_hit = []


@dataclass
class PortfolioState:
    """Aggregate state of a multi-asset portfolio during backtesting."""

    capital: float
    positions: Dict[str, PositionState] = field(default_factory=dict)

    def get_position(self, symbol: str) -> PositionState:
        """Get or create a position state for a symbol."""
        if symbol not in self.positions:
            self.positions[symbol] = PositionState(symbol=symbol)
        return self.positions[symbol]

    def nav(self, prices: Dict[str, float]) -> float:
        """Net asset value: cash + sum of position market values."""
        invested = sum(
            pos.quantity * prices.get(pos.symbol, 0.0)
            for pos in self.positions.values()
            if pos.is_open
        )
        return self.capital + invested

    def invested_value(self, prices: Dict[str, float]) -> float:
        """Total market value of open positions."""
        return sum(
            pos.quantity * prices.get(pos.symbol, 0.0)
            for pos in self.positions.values()
            if pos.is_open
        )

    def position_weight(self, symbol: str, prices: Dict[str, float]) -> float:
        """Current weight of a symbol in the portfolio (0-1)."""
        total = self.nav(prices)
        if total <= 0:
            return 0.0
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open:
            return 0.0
        return (pos.quantity * prices.get(symbol, 0.0)) / total

    def to_strategy_portfolio(self, symbol: str) -> dict:
        """Convert to the legacy portfolio dict expected by Strategy.generate_signals().

        Returns: {"capital": float, "position_qty": float, "entry_price": float, "symbol": str}
        """
        pos = self.positions.get(symbol, PositionState(symbol=symbol))
        return {
            "capital": self.capital,
            "position_qty": pos.quantity,
            "entry_price": pos.entry_price,
            "symbol": symbol,
        }
