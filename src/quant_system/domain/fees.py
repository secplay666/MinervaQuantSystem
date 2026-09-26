from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from .money import mul_half_up
from .rules import MarketRules


@dataclass(frozen=True)
class FeeBreakdown:
    commission_fen: int
    stamp_duty_fen: int
    transfer_fee_fen: int

    @property
    def total_fen(self) -> int:
        return self.commission_fen + self.stamp_duty_fen + self.transfer_fee_fen


class FeeSchedule:
    """Per-order fees; each component is rounded half-up to the fen.

    The broker commission includes exchange handling and regulatory fees and
    has a per-order minimum; stamp duty (sell side) and transfer fee (both
    sides) follow the statutory schedule in the market rules.
    """

    def __init__(self, rules: MarketRules, commission_rate: Fraction, commission_min_fen: int) -> None:
        self.rules = rules
        self.commission_rate = commission_rate
        self.commission_min_fen = commission_min_fen

    def fees(self, side: str, exchange: str, notional_fen: int, day: date) -> FeeBreakdown:
        if notional_fen <= 0:
            return FeeBreakdown(0, 0, 0)
        commission = max(mul_half_up(notional_fen, self.commission_rate), self.commission_min_fen)
        stamp = mul_half_up(notional_fen, self.rules.stamp_duty_rate(side, day))
        transfer = mul_half_up(notional_fen, self.rules.transfer_fee_rate(exchange, day))
        return FeeBreakdown(commission, stamp, transfer)
