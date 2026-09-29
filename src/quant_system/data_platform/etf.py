"""Daily ETF shares outstanding from the two exchanges.

The SSE publishes one table per trading day (about 60 funds in 2015, over
900 now, shares in units of 10,000); the SZSE publishes a date range, at
most six months per request (from 2016-09-30, shares in units of one).
Both are stored in one table keyed by (trade_date, exchange, symbol).

Fetch planning mirrors the corporate windows: the fetch log records what
is complete, and both ingestion and rebuild decide completeness from the
run id's date, so a rebuilt log equals the live one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .normalization import _lineage, _to_date, as_date

__all__ = [
    "ETF_SHARE_COLUMNS",
    "SOURCE_ETF_SSE",
    "SOURCE_ETF_SZSE",
    "etf_fetch_log_row",
    "merge_etf_shares",
    "normalize_etf_sse",
    "normalize_etf_szse",
    "sse_etf_dates",
    "sse_window",
    "szse_etf_months",
    "szse_window",
]

SOURCE_ETF_SSE = "sse.commonQuery.ETFGM"
SOURCE_ETF_SZSE = "akshare.fund_scale_daily_szse.szse"
ETF_SHARE_COLUMNS = [
    "trade_date", "exchange", "symbol", "name", "etf_type", "shares",
    "source", "ingested_at", "run_id", "schema_version",
]
SSE_UNIT = 10_000  # TOT_VOL is in units of 10,000 shares
SSE_EMPTY_FINAL_DAYS = 30  # an empty day this old is not published late; it has no data
SZSE_FINAL_DAYS = 7  # a month fetched this long after its end is complete


def sse_window(day: date) -> str:
    return f"sse_{day:%Y%m%d}"


def szse_window(month: date) -> str:
    return f"szse_{month:%Y%m}"


def run_date(run_id: str) -> date:
    return date(int(run_id[:4]), int(run_id[4:6]), int(run_id[6:8]))


def _month_end(month: date) -> date:
    following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _done(log: pd.DataFrame | None, dataset: str) -> pd.DataFrame:
    if log is None or log.empty:
        return pd.DataFrame(columns=["window", "run_id"])
    return log.loc[log["dataset"] == dataset, ["window", "run_id"]]


def sse_etf_dates(open_dates: list[date], start: date, latest: date, log: pd.DataFrame | None,
                  limit: int) -> tuple[list[date], int]:
    """Open dates still to fetch, newest first, at most ``limit``; and how
    many more are left for later runs."""
    done = set(_done(log, "etf_sse")["window"])
    pending = [day for day in open_dates if start <= day <= latest and sse_window(day) not in done]
    pending.sort(reverse=True)
    return pending[:limit], max(0, len(pending) - limit)


def szse_etf_months(start: date, latest: date, log: pd.DataFrame | None) -> list[tuple[date, date]]:
    """(first, last) day of each month not yet complete, oldest first."""
    final = set()
    for window, run_id in _done(log, "etf_szse").itertuples(index=False):
        month = date(int(window[5:9]), int(window[9:11]), 1)
        if run_date(run_id) > _month_end(month) + timedelta(days=SZSE_FINAL_DAYS):
            final.add(window)
    months, current = [], date(start.year, start.month, 1)
    while current <= latest:
        if szse_window(current) not in final:
            months.append((max(current, start), min(_month_end(current), latest)))
        current = _month_end(current) + timedelta(days=1)
    return months


def etf_fetch_log_row(window: str, rows: int, run_id: str) -> dict | None:
    """Fetch-log entry for one response, or None while an empty SSE day may
    still be published."""
    if window.startswith("sse_"):
        day = date(int(window[4:8]), int(window[8:10]), int(window[10:12]))
        if rows == 0 and (run_date(run_id) - day).days <= SSE_EMPTY_FINAL_DAYS:
            return None
        return {"dataset": "etf_sse", "window": window, "rows": rows, "run_id": run_id}
    return {"dataset": "etf_szse", "window": window, "rows": rows, "run_id": run_id}


def _finish(frame: pd.DataFrame, source: str, run_id: str, ingested_at: str) -> tuple[pd.DataFrame, dict]:
    frame = frame.copy()
    bad = frame["trade_date"].isna() | ~frame["symbol"].str.fullmatch(r"\d{6}").fillna(False)
    bad |= ~(frame["shares"] > 0)
    counts = {"dropped_invalid": int(bad.sum())}
    frame = frame[~bad]
    before = len(frame)
    frame = frame.drop_duplicates(["trade_date", "exchange", "symbol"], keep="last")
    counts["dropped_duplicate"] = before - len(frame)
    frame = _lineage(frame, source, run_id, ingested_at)
    return frame[ETF_SHARE_COLUMNS].reset_index(drop=True), counts


def normalize_etf_sse(raw: pd.DataFrame, day: date, run_id: str, ingested_at: str) -> tuple[pd.DataFrame, dict]:
    """One SSE day.  Rows stamped with another date are dropped and counted."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=ETF_SHARE_COLUMNS), {"dropped_other_date": 0}
    stamped = _to_date(raw["STAT_DATE"])
    other = stamped != day
    raw, stamped = raw[~other], stamped[~other]
    frame = pd.DataFrame({
        "trade_date": stamped,
        "exchange": "SSE",
        "symbol": raw["SEC_CODE"].astype("string").str.strip().str.zfill(6),
        "name": raw["SEC_NAME"].astype("string").str.strip(),
        "etf_type": raw["ETF_TYPE"].astype("string").str.strip() if "ETF_TYPE" in raw else pd.NA,
        "shares": pd.to_numeric(raw["TOT_VOL"], errors="coerce") * SSE_UNIT,
    })
    frame, counts = _finish(frame, SOURCE_ETF_SSE, run_id, ingested_at)
    counts["dropped_other_date"] = int(other.sum())
    return frame, counts


def normalize_etf_szse(raw: pd.DataFrame, run_id: str, ingested_at: str) -> tuple[pd.DataFrame, dict]:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=ETF_SHARE_COLUMNS), {}
    frame = pd.DataFrame({
        "trade_date": _to_date(raw["日期"]),
        "exchange": "SZSE",
        "symbol": raw["基金代码"].astype("string").str.strip().str.zfill(6),
        "name": raw["基金简称"].astype("string").str.strip(),
        "etf_type": pd.Series(pd.NA, index=raw.index, dtype="string"),
        "shares": pd.to_numeric(raw["基金份额"].astype("string").str.replace(",", "", regex=False),
                                errors="coerce"),
    })
    return _finish(frame, SOURCE_ETF_SZSE, run_id, ingested_at)


def merge_etf_shares(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """A later fetch of the same (day, fund) replaces the earlier one."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=ETF_SHARE_COLUMNS)
    frame = pd.concat([part.reindex(columns=ETF_SHARE_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values("run_id", kind="stable").drop_duplicates(["trade_date", "exchange", "symbol"],
                                                                       keep="last")
    frame["trade_date"] = [as_date(value) for value in frame["trade_date"]]
    return frame.sort_values(["trade_date", "exchange", "symbol"], ignore_index=True)
