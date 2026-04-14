#!/usr/bin/env python3
"""Data classes for exit signals and risk events."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ExitSignal:
    """A signal to exit (partially or fully) a position."""
    symbol: str
    exit_type: str  # "stop_loss", "trailing_stop", "take_profit"
    quantity_pct: float  # fraction of position to sell (0-1)
    reason: str
    current_price: float = 0.0
    trigger_price: float = 0.0
    urgency: str = "normal"  # "normal", "high" (circuit breaker)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def label(self) -> str:
        return f"{self.exit_type.upper()} {self.symbol} ({self.quantity_pct:.0%})"


@dataclass
class CircuitBreaker:
    """Portfolio-level drawdown circuit breaker."""
    triggered_at: datetime
    portfolio_value: float
    peak_value: float
    drawdown_pct: float

    @property
    def message(self) -> str:
        return (
            f"CIRCUIT BREAKER: portfolio drawdown {self.drawdown_pct:.1%} "
            f"(value ${self.portfolio_value:.2f}, peak ${self.peak_value:.2f})"
        )


@dataclass
class RiskViolation:
    """A risk limit violation (informational or actionable)."""
    violation_type: str  # "position_concentration", "sector_exposure"
    detail: str
    current_value: float
    limit_value: float
