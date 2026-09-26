"""Entities on the signal-to-fill boundary (ARCHITECTURE §7.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class TargetPortfolio:
    """What a strategy wants to hold after the close of ``as_of``."""

    as_of: date
    weights: dict[str, float]
    explanations: dict[str, dict[str, object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if any(weight < 0 for weight in self.weights.values()) or total > 1 + 1e-9:
            raise ValueError(f"target weights must be non-negative and sum to <= 1, got {total}")


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    session: date
    symbol: str
    side: str
    quantity: int
    reason: str
    rank: int = 0


@dataclass(frozen=True)
class RiskDecision:
    """A structured reason why an order was reduced or rejected."""

    session: date
    order_id: str
    symbol: str
    side: str
    rule_id: str
    decision: str  # "rejected" | "reduced" | "flagged"
    actual: str
    limit: str
    reason: str
