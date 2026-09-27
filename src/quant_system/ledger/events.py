"""Ledger events; every event has a stable id so imports are idempotent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Union


@dataclass(frozen=True)
class CashDeposited:
    event_id: str
    session: date
    amount_fen: int


@dataclass(frozen=True)
class TradeFilled:
    event_id: str
    session: date
    symbol: str
    side: str  # "buy" | "sell"
    quantity: int
    price_fen: int
    notional_fen: int
    commission_fen: int
    stamp_duty_fen: int
    transfer_fee_fen: int

    @property
    def fees_fen(self) -> int:
        return self.commission_fen + self.stamp_duty_fen + self.transfer_fee_fen

    @property
    def cash_delta_fen(self) -> int:
        if self.side == "buy":
            return -(self.notional_fen + self.fees_fen)
        return self.notional_fen - self.fees_fen


@dataclass(frozen=True)
class SharesAdjusted:
    """Corporate action on the ex-date: shares scaled by the hfq step.

    Fractional shares are paid out as cash in lieu at the reference price,
    so position value is continuous and share counts stay integral.
    """

    event_id: str
    session: date
    symbol: str
    old_quantity: int
    new_quantity: int
    cash_in_lieu_fen: int
    step: float


@dataclass(frozen=True)
class PositionDelisted:
    event_id: str
    session: date
    symbol: str
    quantity: int
    settlement_price_fen: int
    proceeds_fen: int


@dataclass(frozen=True)
class SessionClosed:
    """End of session: shares bought today become sellable (T+1)."""

    event_id: str
    session: date


@dataclass(frozen=True)
class PositionAdjusted:
    """A holding corrected to what the broker reports (stage 4 manual
    accounts, ADR-008 §9).  The new quantity counts as settled (sellable) and
    ``cost_fen`` is the cost basis of the whole new position."""

    event_id: str
    session: date
    symbol: str
    old_quantity: int
    new_quantity: int
    cost_fen: int


@dataclass(frozen=True)
class CashAdjusted:
    """Signed cash correction or withdrawal; cash may not go negative."""

    event_id: str
    session: date
    amount_fen: int


LedgerEvent = Union[CashDeposited, TradeFilled, SharesAdjusted, PositionDelisted, SessionClosed, PositionAdjusted,
                    CashAdjusted]
