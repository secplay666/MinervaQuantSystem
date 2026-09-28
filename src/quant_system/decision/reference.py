"""Reference facts the decision needs beyond the price panels: ex-dates,
announced suspensions, risk-warning changes and newly published periodic
reports on a given session.

``FrameReference`` works on plain frames (tests, fixtures); ``load_reference``
reads them from ``market.duckdb`` with a read-only connection.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd


def _dates(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    frame = frame.copy()
    for column in columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.date
    return frame


class FrameReference:
    def __init__(self, dividends: pd.DataFrame | None = None, suspensions: pd.DataFrame | None = None,
                 risk_intervals: pd.DataFrame | None = None, reports: pd.DataFrame | None = None) -> None:
        self.dividends = _dates(dividends if dividends is not None else pd.DataFrame(columns=["symbol", "ex_date"]),
                                ("ex_date",))
        self.suspensions = _dates(
            suspensions if suspensions is not None
            else pd.DataFrame(columns=["symbol", "suspend_start", "suspend_end", "expected_resume"]),
            ("suspend_start", "suspend_end", "expected_resume"))
        self.risk_intervals = _dates(
            risk_intervals if risk_intervals is not None
            else pd.DataFrame(columns=["symbol", "status", "start_date", "end_date"]),
            ("start_date", "end_date"))
        self.reports = _dates(  # one row per (symbol, report period): the first announcement
            reports if reports is not None else pd.DataFrame(columns=["symbol", "report_date", "notice_date"]),
            ("report_date", "notice_date"))

    def reports_published(self, after: date | None, through: date) -> dict[str, list[date]]:
        """{symbol: [report periods]} first announced in (after, through]."""
        r = self.reports
        mask = r["notice_date"].notna() & (r["notice_date"] <= through)
        if after is not None:
            mask &= r["notice_date"] > after
        out: dict[str, list[date]] = {}
        for symbol, period in sorted(zip(r.loc[mask, "symbol"].astype(str), r.loc[mask, "report_date"])):
            out.setdefault(symbol, []).append(period)
        return out

    def ex_dates(self, day: date) -> set[str]:
        """Symbols going ex-rights or ex-dividend on ``day`` (implemented plans only, ADR-004)."""
        return set(self.dividends.loc[self.dividends["ex_date"] == day, "symbol"].astype(str))

    def suspended(self, day: date) -> set[str]:
        """Symbols with an announced suspension covering ``day``: starting on
        ``day``, or started earlier with an end or expected resumption after
        it (end date inclusive).  Vendor records without either date are
        mostly stale (the Baidu calendar rarely fills them in), so they are
        ignored; a suspension still in force shows up as a missing bar on T."""
        s = self.suspensions
        if "expected_resume" not in s:
            s = s.assign(expected_resume=pd.NaT)
        started = s["suspend_start"].notna() & (s["suspend_start"] <= day)
        today = s["suspend_start"] == day
        until = s["suspend_end"].notna() & (s["suspend_end"] >= day)
        resume = s["suspend_end"].isna() & s["expected_resume"].notna() & (s["expected_resume"] > day)
        return set(s.loc[started & (today | until | resume), "symbol"].astype(str))

    def risk_starting(self, day: date) -> dict[str, str]:
        """{symbol: status} for risk-warning intervals starting on ``day``."""
        r = self.risk_intervals
        rows = r[r["start_date"] == day]
        return {str(symbol): str(status) for symbol, status in zip(rows["symbol"], rows["status"])}


REPORT_LOOKBACK = timedelta(days=31)  # reports announced since the previous session, holidays included


def load_reference(database: Path, since: date) -> FrameReference:
    """Rows from ``since`` onwards (the decision only looks at T and T+1);
    periodic reports from a month earlier, so announcements on weekends and
    holidays before T are still seen."""
    with duckdb.connect(str(database), read_only=True) as con:
        dividends = con.execute("SELECT symbol, ex_date FROM dividends WHERE ex_date >= ?", [since]).fetchdf()
        suspensions = con.execute(
            "SELECT symbol, suspend_start, suspend_end, expected_resume FROM suspension_events "
            "WHERE suspend_end IS NULL OR suspend_end >= ?", [since]).fetchdf()
        risk = con.execute("SELECT symbol, status, start_date, end_date FROM risk_warning_intervals "
                           "WHERE start_date >= ?", [since]).fetchdf()
        reports = None
        if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'fin_income'").fetchone()[0]:
            reports = con.execute(
                "SELECT symbol, report_date, MIN(notice_date) AS notice_date FROM fin_income "
                "GROUP BY symbol, report_date HAVING MIN(notice_date) >= ?", [since - REPORT_LOOKBACK]).fetchdf()
    return FrameReference(dividends, suspensions, risk, reports)
