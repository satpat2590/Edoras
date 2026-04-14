"""
Signal dataclass — structured replacement for raw signal dicts.

Backward-compatible: existing strategies can keep returning
[{"action": "BUY", "weight": 0.7, "reason": "..."}] and the engine
will auto-wrap them via Signal.from_dict().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""

    action: str  # "BUY", "SELL", "REDUCE", "CLOSE"
    weight: float = 0.5  # 0-1, position sizing weight
    confidence: float = 1.0  # 0-1, separate from weight (for filtering/ranking)
    reason: str = ""
    target_position_pct: Optional[float] = None  # e.g. 0.5 = reduce to 50% of current
    symbols: Optional[List[str]] = None  # for multi-symbol signals (pairs trading)
    metadata: dict = field(default_factory=dict)  # regime, indicators, etc.
    urgency: str = "normal"  # "immediate", "normal", "patient"

    def to_dict(self) -> dict:
        """Backward-compatible dict representation (matches legacy format)."""
        return {"action": self.action, "weight": self.weight, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> Signal:
        """Construct from a legacy signal dict."""
        return cls(
            action=d["action"],
            weight=d.get("weight", 0.5),
            reason=d.get("reason", ""),
            confidence=d.get("confidence", 1.0),
            target_position_pct=d.get("target_position_pct"),
            symbols=d.get("symbols"),
            metadata=d.get("metadata", {}),
            urgency=d.get("urgency", "normal"),
        )

    def __repr__(self) -> str:
        parts = [f"{self.action} w={self.weight:.2f}"]
        if self.confidence < 1.0:
            parts.append(f"conf={self.confidence:.2f}")
        if self.reason:
            parts.append(self.reason[:60])
        return f"Signal({', '.join(parts)})"
