from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from ..domain.entities import TargetPortfolio


class Strategy(Protocol):
    """Deterministic: the same code, parameters and view give the same target.

    ``on_close`` runs after the close of ``view.session`` and may only read
    data through the view (which refuses anything later).  A strategy that
    sets ``wants_portfolio_state = True`` also receives the holdings after
    that close: ``on_close(view, state)``.
    """

    strategy_id: str
    version: str

    def on_close(self, view) -> TargetPortfolio:  # view: backtest.view.PanelView
        ...


@dataclass(frozen=True)
class PortfolioState:
    """Holdings after the close of ``session``, marked at that close.

    Marks are the close, or the reference price for a name without a bar
    (ADR-002 §4), so ``weights`` sum to 1 - cash / nav.
    """

    session: date
    nav_fen: int
    cash_fen: int
    quantities: Mapping[str, int]
    weights: Mapping[str, float]
    previous_target: TargetPortfolio | None


class RebalanceSchedule(Protocol):
    def is_rebalance(self, calendar, index: int) -> bool:
        ...


class MonthEndSchedule:
    """Rebalance after the close of each month's last session."""

    def is_rebalance(self, calendar, index: int) -> bool:
        return calendar.is_month_end(index)


@dataclass(frozen=True)
class EveryNSessionsSchedule:
    """Rebalance every ``every`` sessions, counted from the calendar start
    (not the backtest start) so the dates do not depend on the window."""

    every: int

    def __post_init__(self) -> None:
        if self.every < 1:
            raise ValueError("schedule.every must be >= 1")

    def is_rebalance(self, calendar, index: int) -> bool:
        return (index + 1) % self.every == 0


def build_schedule(spec: Mapping[str, Any] | None) -> RebalanceSchedule:
    spec = dict(spec or {"type": "month_end"})
    kind = spec.get("type", "month_end")
    if kind == "month_end":
        return MonthEndSchedule()
    if kind == "every_n_sessions":
        return EveryNSessionsSchedule(int(spec["every"]))
    raise ValueError(f"unknown schedule type {kind!r}")
