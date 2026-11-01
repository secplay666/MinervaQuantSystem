"""Recovery of failures reported in the notification centre.

The evening update retries itself (the second firing of the timer) and a day's decision may be rerun.  When a
later run succeeds, the critical events of the failure are marked resolved — the home page's 待办 no longer lists
them; the notification centre keeps them, marked 已恢复 — and one info event says what recovered.  Nothing is
resolved by hand: an event stays open until a run shows the problem gone.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..data_platform.sessions import SHANGHAI_TZ
from .db.base import utc_now
from .db.models import Event


def open_critical(category: str | None = None, prefix: str | None = None, account_id: str | None = None,
                  trade_date: date | None = None) -> Select:
    """Unresolved critical events, narrowed by category, event-id prefix, account and trade date."""
    query = select(Event).where(Event.level == "critical", Event.resolved_at.is_(None))
    if category:
        query = query.where(Event.category == category)
    if prefix:
        query = query.where(Event.event_id.startswith(prefix, autoescape=True))
    if account_id:
        query = query.where(Event.account_id == account_id)
    if trade_date:
        query = query.where(Event.trade_date == trade_date)
    return query.order_by(Event.at, Event.event_id)


def recover(session: Session, query: Select, event_id: str, title: str, resolution: str, *, category: str,
            run_id: str | None = None, trade_date: date | None = None, account_id: str | None = None,
            at: datetime | None = None) -> list[Event]:
    """Resolve the events of ``query`` with ``resolution`` and, when there were any, add one info event
    ``event_id`` naming them.  Returns the events resolved (none: nothing was open, nothing is added)."""
    now = at or utc_now()
    resolved = list(session.scalars(query))
    for event in resolved:
        event.resolved_at, event.resolution = now, resolution
    if resolved and session.get(Event, event_id) is None:
        lines = [f"{e.at.astimezone(SHANGHAI_TZ):%m-%d %H:%M} {e.title}" for e in resolved]
        session.add(Event(event_id=event_id, at=now, level="info", category=category, title=title,
                          body=f"{resolution}。已恢复的通知：\n" + "\n".join(lines), run_id=run_id,
                          trade_date=trade_date, account_id=account_id))
    return resolved
