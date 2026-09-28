"""What a decision run is based on: the latest ingest run, the exchange
calendar published with it, and the decision session T.

The canonical trading calendar stops at the last final session, so it
cannot say whether T is a month end (``TradingCalendar.is_month_end`` treats
the last known session as one).  The raw calendar saved by the ingest run
lists the exchange's sessions through the end of the published year; that
is the calendar a decision uses (ADR-008 §3).
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..data_platform.normalization import calendar_open_dates
from ..data_platform.sessions import DEFAULT_FINAL_TIME, latest_final_session, parse_hhmm

INGEST_MODES = {"incremental", "full"}
RAW_CALENDAR = Path("data") / "raw" / "akshare" / "trading_calendar"


@dataclass(frozen=True)
class IngestRecord:
    run_id: str
    status: str
    expected_latest: date | None
    data_version: str | None
    mode: str


def latest_ingest(root: Path) -> IngestRecord | None:
    """Newest ingest run (incremental or full; rebuilds are not ingests)."""
    for path in sorted((root / "data" / "manifests").glob("*.json"), reverse=True):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("mode") not in INGEST_MODES:
            continue
        latest = payload.get("expected_latest_date")
        return IngestRecord(run_id=payload["run_id"], status=payload.get("status", "unknown"),
                            expected_latest=date.fromisoformat(latest) if latest else None,
                            data_version=payload.get("data_version"), mode=payload["mode"])
    return None


def exchange_calendar(root: Path, run_id: str) -> list[date]:
    """Sessions (including future ones) from the raw calendar of ``run_id``."""
    directory = root / RAW_CALENDAR / f"run_id={run_id}"
    files = sorted(directory.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no raw trading calendar saved by run {run_id} ({directory})")
    return calendar_open_dates(pd.read_parquet(files[0]))


def final_time(root: Path) -> object:
    config = root / "configs" / "data_platform.json"
    if config.exists():
        value = json.loads(config.read_text(encoding="utf-8")).get("session_final_time")
        if value:
            return parse_hhmm(value)
    return DEFAULT_FINAL_TIME


def decision_session(calendar: list[date], now: datetime, final=DEFAULT_FINAL_TIME) -> date | None:
    """The latest session whose bar is final at ``now`` (the session T a decision is for)."""
    return latest_final_session(calendar, now, final)


def next_session(calendar: list[date], day: date) -> date | None:
    index = bisect_right(calendar, day)
    return calendar[index] if index < len(calendar) else None


def is_month_end(calendar: list[date], day: date) -> bool:
    """Last session of its month by the published calendar.  When the next
    session is not published yet (next year), December's last session still
    counts, since no later session can fall in the same month."""
    following = next_session(calendar, day)
    if following is None:
        if day.month == 12:
            return True
        raise ValueError(f"the exchange calendar ends at {calendar[-1]}; cannot tell whether {day} is a month end")
    return (day.year, day.month) != (following.year, following.month)


def is_rebalance_day(schedule: dict | None, calendar: list[date], data_sessions: tuple[date, ...], day: date) -> bool:
    """Whether ``day`` is a rebalance session of the strategy schedule
    (``strategy.base.build_schedule`` semantics, using the full calendar)."""
    spec = dict(schedule or {"type": "month_end"})
    kind = spec.get("type", "month_end")
    if kind == "month_end":
        return is_month_end(calendar, day)
    if kind == "every_n_sessions":
        index = data_sessions.index(day)  # counted from the data calendar start, as in backtests
        return (index + 1) % int(spec["every"]) == 0
    raise ValueError(f"unknown schedule type {kind!r}")
