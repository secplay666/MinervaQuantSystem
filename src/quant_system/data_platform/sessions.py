"""Trading-session time rules (Asia/Shanghai).

Daily bars are only final after the close plus a settle buffer: ChiNext/STAR
after-hours fixed-price trading runs until 15:30, and vendors publish the
final bar some time later.  Any run before the cutoff treats the current
session as not yet available.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SESSION_OPEN = time(9, 15)
DEFAULT_FINAL_TIME = time(16, 0)


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def latest_final_session(
    open_dates: Sequence[date],
    now: datetime,
    final_time: time = DEFAULT_FINAL_TIME,
    explicit_end: date | None = None,
) -> date | None:
    """Return the most recent session whose daily bar is final at ``now``.

    ``open_dates`` must be sorted and may include future sessions.
    """
    local = now.astimezone(SHANGHAI_TZ)
    cutoff = local.date()
    index = bisect_right(open_dates, cutoff)
    if index and open_dates[index - 1] == cutoff and local.time() < final_time:
        index -= 1
    if explicit_end is not None:
        index = min(index, bisect_right(open_dates, explicit_end))
    return open_dates[index - 1] if index else None


def session_offset(open_dates: Sequence[date], anchor: date, sessions_back: int) -> date:
    """Return the session ``sessions_back`` sessions before ``anchor``.

    ``anchor`` itself counts as offset 0 when it is a session; dates before the
    calendar start clamp to the first session.
    """
    index = bisect_left(open_dates, anchor)
    if index >= len(open_dates) or open_dates[index] != anchor:
        index -= 1
    return open_dates[max(0, index - sessions_back)]


def snapshot_session(
    open_dates: Sequence[date],
    fetched_at: datetime,
    final_time: time = DEFAULT_FINAL_TIME,
) -> tuple[date | None, str]:
    """Label a market-wide snapshot by the session it describes.

    Returns ``(session_date, kind)`` where kind is ``"post_close"`` or
    ``"intraday"``; intraday snapshots are not end-of-day facts and get no
    session date.
    """
    local = fetched_at.astimezone(SHANGHAI_TZ)
    today = local.date()
    index = bisect_left(open_dates, today)
    is_session_day = index < len(open_dates) and open_dates[index] == today
    if is_session_day and SESSION_OPEN <= local.time() < final_time:
        return None, "intraday"
    return latest_final_session(open_dates, fetched_at, final_time), "post_close"
