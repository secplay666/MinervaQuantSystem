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

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .normalization import _lineage, _to_date, as_date, normalize_index_bars

__all__ = [
    "ETF_SHARE_COLUMNS",
    "SOURCE_ETF_SSE",
    "SOURCE_ETF_SZSE",
    "ETF_MASTER_COLUMNS",
    "SOURCE_ETF_LIST_SSE",
    "SOURCE_ETF_LIST_SZSE",
    "etf_fetch_log_row",
    "etf_prefix",
    "group_members",
    "load_etf_groups",
    "merge_etf_master",
    "merge_etf_shares",
    "normalize_etf_bars",
    "normalize_etf_list_sse",
    "normalize_etf_list_szse",
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


# ---------------------------------------------------------------------------
# ETF lists: each fund's tracking index, from both exchanges
# ---------------------------------------------------------------------------

SOURCE_ETF_LIST_SSE = "sse.commonSoaQuery.FUND_LIST"
SOURCE_ETF_LIST_SZSE = "szse.ShowReport.1945"
ETF_MASTER_COLUMNS = [
    "exchange", "symbol", "name", "full_name", "index_code", "index_name", "manager", "list_date",
    "listed_run_id", "source", "ingested_at", "run_id", "schema_version",
]


def normalize_etf_list_sse(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=ETF_MASTER_COLUMNS)
    def text(column: str) -> pd.Series:
        if column not in raw:
            return pd.Series(pd.NA, index=raw.index, dtype="string")
        return raw[column].astype("string").str.strip()

    frame = pd.DataFrame({
        "exchange": "SSE",
        "symbol": text("fundCode"),
        "name": text("fundAbbr"),
        "full_name": text("secNameFull"),
        "index_code": text("INDEX_CODE").replace("", pd.NA),
        "index_name": text("INDEX_NAME").replace("", pd.NA),
        "manager": text("companyName"),
        "list_date": _to_date(raw["listingDate"]) if "listingDate" in raw else None,
    })
    return _finish_list(frame, SOURCE_ETF_LIST_SSE, run_id, ingested_at)


def normalize_etf_list_szse(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    """拟合指数 is "<code> <short name>" ("399006 创业板指"), or a bare code
    for foreign indices ("HSTECH")."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=ETF_MASTER_COLUMNS)
    tracked = raw["拟合指数"].astype("string").str.strip()
    frame = pd.DataFrame({
        "exchange": "SZSE",
        "symbol": raw["证券代码"].astype("string").str.strip().str.zfill(6),
        "name": raw["证券简称"].astype("string").str.strip(),
        "full_name": pd.Series(pd.NA, index=raw.index, dtype="string"),
        "index_code": tracked.str.split(n=1).str[0].replace("", pd.NA),
        "index_name": tracked.str.split(n=1).str[1].str.strip(),
        "manager": raw["基金管理人"].astype("string").str.strip(),
        "list_date": None,
    })
    return _finish_list(frame, SOURCE_ETF_LIST_SZSE, run_id, ingested_at)


def _finish_list(frame: pd.DataFrame, source: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    frame = frame[frame["symbol"].str.fullmatch(r"\d{6}").fillna(False)].drop_duplicates("symbol", keep="last")
    frame["listed_run_id"] = run_id
    frame = _lineage(frame.copy(), source, run_id, ingested_at)
    return frame[ETF_MASTER_COLUMNS].reset_index(drop=True)


def merge_etf_master(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """The latest listing of each fund wins; a fund no longer listed keeps
    its last record (``listed_run_id`` tells when it was last seen), so the
    history of a closed fund still maps to its index."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=ETF_MASTER_COLUMNS)
    frame = pd.concat([part.reindex(columns=ETF_MASTER_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values("run_id", kind="stable")
    # SZSE lists carry no listing date: keep the one learned earlier, if any.
    known = frame.dropna(subset=["list_date"]).groupby(["exchange", "symbol"])["list_date"].last()
    frame = frame.drop_duplicates(["exchange", "symbol"], keep="last").set_index(["exchange", "symbol"])
    frame["list_date"] = frame["list_date"].where(frame["list_date"].notna(), known.reindex(frame.index))
    return frame.reset_index()[ETF_MASTER_COLUMNS].sort_values(["exchange", "symbol"], ignore_index=True)


# ---------------------------------------------------------------------------
# Broad-index groups (configs/etf/broad_groups.json)
# ---------------------------------------------------------------------------

def load_etf_groups(path: str | Path) -> dict:
    """{exclude_name_pattern, groups: [{id, name, index_codes, chart_symbol}]}, groups in display order."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for group in payload["groups"]:
        missing = {"id", "name", "index_codes", "chart_symbol"} - set(group)
        if missing:
            raise ValueError(f"ETF group {group.get('id')!r} lacks {sorted(missing)}")
    return payload


def group_members(master: pd.DataFrame | None, config: dict) -> pd.DataFrame:
    """exchange, symbol, name, group_id for every plain (not enhanced) fund
    tracking a group's index."""
    columns = ["exchange", "symbol", "name", "group_id", "list_date", "listed_run_id"]
    if master is None or master.empty:
        return pd.DataFrame(columns=columns)
    by_code = {code: group["id"] for group in config["groups"] for code in group["index_codes"]}
    frame = master.assign(group_id=master["index_code"].map(by_code))
    frame = frame[frame["group_id"].notna()]
    pattern = config.get("exclude_name_pattern")
    if pattern:
        frame = frame[~frame["name"].fillna("").str.contains(pattern, regex=True)]
    # Hand corrections: {"include": {code: group_id}, "exclude": [code]}.
    overrides = config.get("overrides") or {}
    frame = frame[~frame["symbol"].isin(set(overrides.get("exclude") or []))]
    extra = overrides.get("include") or {}
    if extra:
        added = master[master["symbol"].isin(set(extra)) & ~master["symbol"].isin(set(frame["symbol"]))]
        frame = pd.concat([frame, added.assign(group_id=added["symbol"].map(extra))], ignore_index=True)
    return frame[["exchange", "symbol", "name", "group_id", "list_date", "listed_run_id"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# ETF daily bars (Tencent, like the indices)
# ---------------------------------------------------------------------------

def etf_prefix(symbol: str) -> str:
    """Exchange prefix of a fund code: SSE funds start with 5, SZSE with 1."""
    return "sh" if symbol.startswith(("5", "6")) else "sz"


def normalize_etf_bars(raw: pd.DataFrame, symbol: str, start: date, end: date, run_id: str,
                       ingested_at: str, source: str) -> pd.DataFrame:
    """Index-bar layout keyed by the plain fund code (``name`` is the code
    too; names live in etf_master and change).  Tencent reports fund volume
    in lots and AKShare converts it (it only skips index prefixes)."""
    frame = normalize_index_bars(raw, etf_prefix(symbol) + symbol, symbol, start, end, run_id, ingested_at,
                                 source=source)
    frame["symbol"] = symbol
    return frame
