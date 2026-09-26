from __future__ import annotations

from typing import Protocol

from ..domain.entities import TargetPortfolio


class Strategy(Protocol):
    """Deterministic: the same code, parameters and view give the same target.

    ``on_close`` runs after the close of ``view.session`` and may only read
    data through the view (which refuses anything later).
    """

    strategy_id: str
    version: str

    def on_close(self, view) -> TargetPortfolio:  # view: backtest.view.PanelView
        ...


class RebalanceSchedule(Protocol):
    def is_rebalance(self, calendar, index: int) -> bool:
        ...


class MonthEndSchedule:
    """Rebalance after the close of each month's last session."""

    def is_rebalance(self, calendar, index: int) -> bool:
        return calendar.is_month_end(index)
