"""Market rules by effective date, loaded from configuration (never hard-coded).

Risk states: ``normal``, ``risk_warning`` (ST/*ST), ``delisting_period``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from .money import half_up, rate

RISK_STATES = ("normal", "risk_warning", "delisting_period")
BOARDS = ("SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR", "BSE")


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class _Window:
    start: date | None
    end: date | None

    def contains(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)


@dataclass(frozen=True)
class LimitRule:
    boards: frozenset[str]
    risks: frozenset[str]
    window: _Window
    ratio: Fraction


@dataclass(frozen=True)
class NoLimitRule:
    boards: frozenset[str]
    listed_window: _Window
    first_sessions: int


@dataclass(frozen=True)
class LotRule:
    buy_min: int
    buy_step: int
    odd_lot_below: int

    def round_buy(self, quantity: int) -> int:
        """Largest legal buy order quantity not above ``quantity``."""
        if quantity < self.buy_min:
            return 0
        return self.buy_min + (quantity - self.buy_min) // self.buy_step * self.buy_step

    def round_sell(self, quantity: int, holding: int) -> int:
        """Largest legal sell quantity not above ``quantity``.

        Selling everything is always legal (the odd lot goes in one piece).
        A balance below the odd-lot threshold can only be sold in full.
        """
        quantity = min(quantity, holding)
        if quantity <= 0:
            return 0
        if quantity == holding:
            return holding
        if holding < self.odd_lot_below:
            return 0
        return self.round_buy(quantity)


@dataclass(frozen=True)
class RatedWindow:
    keys: frozenset[str]
    window: _Window
    rate: Fraction


class MarketRules:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.tick_fen = int(payload.get("tick_fen", 1))
        self.limits = [
            LimitRule(frozenset(item["boards"]), frozenset(item["risk"]),
                      _Window(_day(item.get("from")), _day(item.get("to"))), rate(item["ratio"]))
            for item in payload["price_limits"]
        ]
        self.no_limit = [
            NoLimitRule(frozenset(item["boards"]),
                        _Window(_day(item.get("listed_from")), _day(item.get("listed_to"))),
                        int(item["first_sessions"]))
            for item in payload.get("no_limit_sessions", [])
        ]
        delisting = payload.get("delisting_period_first_session_no_limit")
        self.delisting_first_session_from = _day(delisting["from"]) if delisting else None
        self.symbol_limits = [
            (frozenset(item["symbols"]), _Window(_day(item.get("from")), _day(item.get("to"))), rate(item["ratio"]))
            for item in payload.get("symbol_limits", [])
        ]
        inferred = payload.get("inferred_risk_warning")
        self.inferred_risk = (
            (frozenset(inferred["boards"]), _Window(_day(inferred.get("from")), _day(inferred.get("to"))),
             rate(inferred["ratio"]))
            if inferred else None
        )
        self.lots = {
            board: LotRule(int(item["buy_min"]), int(item["buy_step"]), int(item["odd_lot_below"]))
            for item in payload["lots"]
            for board in item["boards"]
        }
        self.stamp_duty = [
            RatedWindow(frozenset([item["side"]]), _Window(_day(item.get("from")), _day(item.get("to"))),
                        rate(item["rate"]))
            for item in payload["stamp_duty"]
        ]
        self.transfer_fee = [
            RatedWindow(frozenset(item["exchanges"]), _Window(_day(item.get("from")), _day(item.get("to"))),
                        rate(item["rate"]))
            for item in payload["transfer_fee"]
        ]
        self.validate()

    @classmethod
    def load(cls, path: Path) -> "MarketRules":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    # -- validation ---------------------------------------------------------

    def _boundaries(self) -> list[date]:
        days = {date(2019, 1, 2), date(2030, 12, 31)}
        for window in [rule.window for rule in self.limits] + [item.window for item in self.stamp_duty] + [
            item.window for item in self.transfer_fee
        ]:
            for edge in (window.start, window.end):
                if edge is not None:
                    days.update({edge, date.fromordinal(edge.toordinal() - 1), date.fromordinal(edge.toordinal() + 1)})
        return sorted(days)

    def validate(self) -> None:
        """Every (board, risk, day) has exactly one limit rule, and fee
        schedules have no gaps or overlaps, at every boundary date."""
        for day in self._boundaries():
            for board in BOARDS:
                for risk in RISK_STATES:
                    matches = [rule for rule in self.limits if self._limit_matches(rule, board, risk, day)]
                    if len(matches) != 1:
                        raise ValueError(f"{len(matches)} price-limit rules match {board}/{risk} on {day}")
                if board not in self.lots:
                    raise ValueError(f"no lot rule for {board}")
            if len([item for item in self.stamp_duty if item.window.contains(day)]) != 1:
                raise ValueError(f"stamp duty schedule is ambiguous on {day}")
            for exchange in ("SSE", "SZSE", "BSE"):
                if len([item for item in self.transfer_fee
                        if exchange in item.keys and item.window.contains(day)]) != 1:
                    raise ValueError(f"transfer fee schedule is ambiguous for {exchange} on {day}")

    @staticmethod
    def _limit_matches(rule: LimitRule, board: str, risk: str, day: date) -> bool:
        return board in rule.boards and risk in rule.risks and rule.window.contains(day)

    # -- queries --------------------------------------------------------------

    def limit_ratio(self, board: str, risk: str, day: date) -> Fraction:
        for rule in self.limits:
            if self._limit_matches(rule, board, risk, day):
                return rule.ratio
        raise KeyError(f"no price-limit rule for {board}/{risk} on {day}")

    def symbol_limit_ratio(self, symbol: str, day: date) -> Fraction | None:
        """Security-specific limit (e.g. non-reformed S shares), if any."""
        for symbols, window, ratio in self.symbol_limits:
            if symbol in symbols and window.contains(day):
                return ratio
        return None

    def is_no_limit_session(
        self, board: str, list_date: date | None, sessions_since_listing: int | None
    ) -> bool:
        """IPO windows without a price limit (``sessions_since_listing`` is 0
        on the listing day; None when the listing predates the data)."""
        if list_date is None or sessions_since_listing is None:
            return False
        return any(
            board in rule.boards and rule.listed_window.contains(list_date)
            and sessions_since_listing < rule.first_sessions
            for rule in self.no_limit
        )

    def delisting_first_session_unlimited(self, day: date) -> bool:
        return self.delisting_first_session_from is not None and day >= self.delisting_first_session_from

    def inferred_risk_ratio(self, board: str, day: date) -> Fraction | None:
        if self.inferred_risk is None:
            return None
        boards, window, ratio = self.inferred_risk
        return ratio if board in boards and window.contains(day) else None

    def limit_prices(self, reference_fen: int, ratio: Fraction) -> tuple[int, int]:
        """(up, down) limit prices, half-up to the tick, at least one tick wide."""
        up = half_up(Fraction(reference_fen) * (1 + ratio))
        down = half_up(Fraction(reference_fen) * (1 - ratio))
        up = max(up, reference_fen + self.tick_fen)
        down = max(min(down, reference_fen - self.tick_fen), self.tick_fen)
        return up, down

    def lot_rule(self, board: str) -> LotRule:
        return self.lots[board]

    def stamp_duty_rate(self, side: str, day: date) -> Fraction:
        for item in self.stamp_duty:
            if side in item.keys and item.window.contains(day):
                return item.rate
        return Fraction(0)

    def transfer_fee_rate(self, exchange: str, day: date) -> Fraction:
        for item in self.transfer_fee:
            if exchange in item.keys and item.window.contains(day):
                return item.rate
        raise KeyError(f"no transfer fee for {exchange} on {day}")
