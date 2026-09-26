"""Corporate reference data for research (stage 3).

Normalizers and append-only merges for share-capital changes, dividends,
Shenwan industry classification and CSIndex constituent weights, plus the
fetch-window planners.  All are pure functions of raw vendor frames so the
rebuild can replay them (ADR-005).  Point-in-time rules are in ADR-004.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .normalization import SCHEMA_VERSION, _lineage, _to_date, as_date
from .symbols import is_a_share

SOURCE_SHARE_CAPITAL = "eastmoney.datacenter.RPT_F10_EH_EQUITY"
SOURCE_DIVIDENDS = "eastmoney.datacenter.RPT_SHAREBONUS_DET"
SOURCE_SW = "swsresearch.StockClassifyUse_stock"
SOURCE_INDEX_WEIGHTS = "akshare.index_stock_cons_weight_csindex.csindex"

A_SHARE_SUFFIXES = ("SH", "SZ", "BJ")


def _symbols(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip().str.zfill(6)


def _a_share_rows(raw: pd.DataFrame, code: str = "SECURITY_CODE", secucode: str = "SECUCODE") -> pd.Series:
    symbols = _symbols(raw[code])
    mask = symbols.map(lambda s: isinstance(s, str) and is_a_share(s)).astype(bool)
    if secucode in raw.columns:
        mask &= raw[secucode].astype("string").str.split(".").str[-1].isin(A_SHARE_SUFFIXES).fillna(False)
    return mask


def _numbers(raw: pd.DataFrame, column: str) -> pd.Series:
    if column not in raw.columns:
        return pd.Series(np.nan, index=raw.index, dtype="float64")
    return pd.to_numeric(raw[column], errors="coerce").astype("float64")


# ---------------------------------------------------------------------------
# Share capital
# ---------------------------------------------------------------------------

SHARE_CAPITAL_COLUMNS = [
    "symbol", "change_date", "notice_date", "total_shares", "float_a_shares", "limited_a_shares",
    "change_reason", "record_key", "source", "ingested_at", "run_id", "schema_version",
]


def normalize_share_capital(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    """One row per share-capital change (``change_date`` = END_DATE).

    Share counts are in shares.  A change is usable from
    max(first session >= change_date, first session > notice_date).
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=SHARE_CAPITAL_COLUMNS)
    raw = raw[_a_share_rows(raw)]
    frame = pd.DataFrame({
        "symbol": _symbols(raw["SECURITY_CODE"]).astype(str),
        "change_date": _to_date(raw["END_DATE"]),
        "notice_date": _to_date(raw["NOTICE_DATE"]) if "NOTICE_DATE" in raw else None,
        "total_shares": _numbers(raw, "TOTAL_SHARES"),
        "float_a_shares": _numbers(raw, "LISTED_A_SHARES"),
        "limited_a_shares": _numbers(raw, "LIMITED_A_SHARES"),
        "change_reason": raw.get("CHANGE_REASON", pd.Series(None, index=raw.index)).astype("string"),
    })
    frame = frame[frame["change_date"].notna() & (frame["total_shares"] > 0)].copy()
    frame["record_key"] = (frame["symbol"] + "|" + frame["change_date"].astype(str) + "|"
                           + frame["change_reason"].fillna("").astype(str))
    frame = _lineage(frame, SOURCE_SHARE_CAPITAL, run_id, ingested_at)
    return frame[SHARE_CAPITAL_COLUMNS].reset_index(drop=True)


def merge_share_capital(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """Append-only; a later fetch of the same change replaces the earlier one."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=SHARE_CAPITAL_COLUMNS)
    frame = pd.concat([part.reindex(columns=SHARE_CAPITAL_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values("run_id", kind="stable").drop_duplicates("record_key", keep="last")
    return frame.sort_values(["symbol", "change_date", "record_key"], ignore_index=True)


# ---------------------------------------------------------------------------
# Dividends
# ---------------------------------------------------------------------------

DIVIDEND_COLUMNS = [
    "symbol", "report_date", "plan_notice_date", "notice_date", "record_date", "ex_date",
    "cash_per_10", "bonus_per_10", "transfer_per_10", "total_shares", "progress", "plan_profile",
    "record_key", "source", "ingested_at", "run_id", "schema_version",
]


def normalize_dividends(raw: pd.DataFrame, report_date: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    """Dividend plans per report period.

    ``progress`` is the vendor's status *at fetch time*; point-in-time use
    relies on ``ex_date`` only (a plan counts once it went ex), never on
    ``progress``, which would reveal which plans were later implemented.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=DIVIDEND_COLUMNS)
    raw = raw[_a_share_rows(raw)]
    frame = pd.DataFrame({
        "symbol": _symbols(raw["SECURITY_CODE"]).astype(str),
        "report_date": _to_date(raw["REPORT_DATE"]) if "REPORT_DATE" in raw else as_date(report_date),
        "plan_notice_date": _to_date(raw["PLAN_NOTICE_DATE"]) if "PLAN_NOTICE_DATE" in raw else None,
        "notice_date": _to_date(raw["NOTICE_DATE"]) if "NOTICE_DATE" in raw else None,
        "record_date": _to_date(raw["EQUITY_RECORD_DATE"]) if "EQUITY_RECORD_DATE" in raw else None,
        "ex_date": _to_date(raw["EX_DIVIDEND_DATE"]) if "EX_DIVIDEND_DATE" in raw else None,
        "cash_per_10": _numbers(raw, "PRETAX_BONUS_RMB"),
        "bonus_per_10": _numbers(raw, "BONUS_RATIO"),
        "transfer_per_10": _numbers(raw, "IT_RATIO"),
        "total_shares": _numbers(raw, "TOTAL_SHARES"),
        "progress": raw.get("ASSIGN_PROGRESS", pd.Series(None, index=raw.index)).astype("string"),
        "plan_profile": raw.get("IMPL_PLAN_PROFILE", pd.Series(None, index=raw.index)).astype("string"),
    })
    frame["report_date"] = frame["report_date"].fillna(as_date(report_date))
    frame["record_key"] = (frame["symbol"] + "|" + frame["report_date"].astype(str) + "|"
                           + frame["plan_notice_date"].astype(str))
    frame = _lineage(frame, SOURCE_DIVIDENDS, run_id, ingested_at)
    return frame[DIVIDEND_COLUMNS].reset_index(drop=True)


def merge_dividends(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=DIVIDEND_COLUMNS)
    frame = pd.concat([part.reindex(columns=DIVIDEND_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values("run_id", kind="stable").drop_duplicates("record_key", keep="last")
    return frame.sort_values(["symbol", "report_date", "record_key"], ignore_index=True)


# ---------------------------------------------------------------------------
# Shenwan industry classification
# ---------------------------------------------------------------------------

SW_SWITCH_DATE = date(2021, 7, 30)  # SW2021 replaced SW2014 for every stock on this date
INDUSTRY_COLUMNS = [
    "symbol", "start_date", "end_date", "sw_code", "sw_version", "l1_code", "l1_name", "l2_name", "l3_name",
    "mapping", "source", "ingested_at", "run_id", "schema_version",
]
UNCLASSIFIED = "000000"


def sw_history_rows(history: pd.DataFrame) -> pd.DataFrame:
    """symbol, start_date, sw_code from the classification workbook."""
    frame = pd.DataFrame({
        "symbol": _symbols(history["股票代码"]).astype(str),
        "start_date": _to_date(history["计入日期"]),
        "sw_code": history["行业代码"].astype("string").str.strip().str.zfill(6).astype(str),
        "updated_at": pd.to_datetime(history["更新日期"], errors="coerce"),
    })
    frame = frame[frame["start_date"].notna() & frame["symbol"].map(is_a_share)]
    # A reclassification can be re-issued for the same start date; the
    # latest update wins.
    frame = frame.sort_values(["symbol", "start_date", "updated_at"], kind="stable")
    return frame.drop_duplicates(["symbol", "start_date"], keep="last").reset_index(drop=True)


def sw_code_table(codes: pd.DataFrame) -> pd.DataFrame:
    table = pd.DataFrame({
        "code": codes["行业代码"].astype("string").str.strip().str.zfill(6).astype(str),
        "l1_name": codes["一级行业名称"].astype("string"),
        "l2_name": codes["二级行业名称"].astype("string"),
        "l3_name": codes["三级行业名称"].astype("string"),
    })
    return table.drop_duplicates("code", keep="last").set_index("code")


def derive_sw2014_mapping(history: pd.DataFrame, codes: pd.DataFrame) -> pd.DataFrame:
    """Map every SW2014 code to a SW2021 level-1 code.

    Evidence: for stocks classified on both sides of the 2021-07-30 switch,
    the SW2021 level-1 class they moved into.  Codes held only by stocks
    that left before 2021 fall back to the majority of their SW2014 level-2
    (then level-1) group.  The result is reviewed and committed as
    ``configs/industry/sw2014_to_sw2021_l1.json``.
    """
    rows = sw_history_rows(history)
    table = sw_code_table(codes)
    before = rows[rows["start_date"] < SW_SWITCH_DATE].groupby("symbol").tail(1)
    after = rows[(rows["start_date"] >= SW_SWITCH_DATE) & rows["sw_code"].isin(table.index)]
    after = after.groupby("symbol").head(1)
    pairs = before[["symbol", "sw_code"]].merge(after[["symbol", "sw_code"]], on="symbol",
                                                 suffixes=("_2014", "_2021"))
    pairs["l1_code"] = pairs["sw_code_2021"].str[:2] + "0000"

    def majority(frame: pd.DataFrame, key: str) -> pd.DataFrame:
        counts = frame.groupby([key, "l1_code"]).size().rename("stocks").reset_index()
        counts["total"] = counts.groupby(key)["stocks"].transform("sum")
        counts = counts.sort_values([key, "stocks", "l1_code"], ascending=[True, False, True])
        best = counts.groupby(key).head(1)
        best = best.assign(share=best["stocks"] / best["total"])
        return best.set_index(key)[["l1_code", "stocks", "share"]]

    by_code = majority(pairs, "sw_code_2014")
    pairs["l2"] = pairs["sw_code_2014"].str[:4]
    pairs["l1"] = pairs["sw_code_2014"].str[:2]
    by_l2, by_l1 = majority(pairs, "l2"), majority(pairs, "l1")
    old_codes = sorted(set(rows.loc[rows["start_date"] < SW_SWITCH_DATE, "sw_code"]))
    records = []
    for code in old_codes:
        for method, lookup, key in (("transition", by_code, code), ("level2_majority", by_l2, code[:4]),
                                    ("level1_majority", by_l1, code[:2])):
            if key in lookup.index:
                hit = lookup.loc[key]
                records.append({"sw2014_code": code, "l1_code": hit["l1_code"], "method": method,
                                "stocks": int(hit["stocks"]), "share": round(float(hit["share"]), 4)})
                break
        else:
            records.append({"sw2014_code": code, "l1_code": UNCLASSIFIED, "method": "unmapped",
                            "stocks": 0, "share": 0.0})
    return pd.DataFrame(records)


def load_sw2014_mapping(path: Path) -> dict[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {code: item["l1_code"] for code, item in payload["codes"].items()}


def normalize_sw_classification(history: pd.DataFrame, codes: pd.DataFrame, mapping: Mapping[str, str],
                                run_id: str, ingested_at: str) -> tuple[pd.DataFrame, list[str]]:
    """Dated industry intervals [start_date, end_date) with a SW2021 level-1
    class for every date.  Returns (intervals, SW2014 codes missing from the
    mapping)."""
    rows = sw_history_rows(history)
    table = sw_code_table(codes)
    rows = rows.sort_values(["symbol", "start_date"], ignore_index=True)
    end = rows.groupby("symbol")["start_date"].shift(-1)
    rows["end_date"] = end.astype(object).where(end.notna(), None)
    new = rows["start_date"] >= SW_SWITCH_DATE
    rows["sw_version"] = np.where(new, "SW2021", "SW2014")
    rows["mapping"] = np.where(new, "native", "sw2014_to_sw2021_l1")
    rows["l1_code"] = np.where(new, rows["sw_code"].str[:2] + "0000", rows["sw_code"].map(mapping))
    missing = sorted(set(rows.loc[~new & rows["l1_code"].isna(), "sw_code"]))
    rows["l1_code"] = rows["l1_code"].fillna(UNCLASSIFIED)
    unknown_new = new & ~rows["sw_code"].isin(table.index)
    rows.loc[unknown_new, "mapping"] = "native_code_not_in_table"
    rows["l1_name"] = rows["l1_code"].map(table["l1_name"])
    rows["l2_name"] = np.where(new, rows["sw_code"].map(table["l2_name"]), None)
    rows["l3_name"] = np.where(new, rows["sw_code"].map(table["l3_name"]), None)
    frame = _lineage(rows, SOURCE_SW, run_id, ingested_at)
    return frame[INDUSTRY_COLUMNS].reset_index(drop=True), missing


# ---------------------------------------------------------------------------
# CSIndex constituent weights (snapshots; no history is published)
# ---------------------------------------------------------------------------

INDEX_WEIGHT_COLUMNS = ["index_code", "as_of_date", "symbol", "name", "exchange", "weight",
                        "source", "ingested_at", "run_id", "schema_version"]


def normalize_index_weights(raw: pd.DataFrame, index_code: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=INDEX_WEIGHT_COLUMNS)
    frame = pd.DataFrame({
        "index_code": index_code,
        "as_of_date": _to_date(raw["日期"]),
        "symbol": _symbols(raw["成分券代码"]).astype(str),
        "name": raw["成分券名称"].astype("string"),
        "exchange": raw["交易所"].astype("string"),
        "weight": pd.to_numeric(raw["权重"], errors="coerce") / 100.0,
    })
    frame = _lineage(frame, SOURCE_INDEX_WEIGHTS, run_id, ingested_at)
    return frame[INDEX_WEIGHT_COLUMNS].reset_index(drop=True)


def merge_index_weights(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=INDEX_WEIGHT_COLUMNS)
    frame = pd.concat([part.reindex(columns=INDEX_WEIGHT_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.sort_values("run_id", kind="stable")
    frame = frame.drop_duplicates(["index_code", "as_of_date", "symbol"], keep="last")
    return frame.sort_values(["index_code", "as_of_date", "symbol"], ignore_index=True)


# ---------------------------------------------------------------------------
# Fetch planning (logged per window so a rebuild can replay the same set)
# ---------------------------------------------------------------------------

FETCH_LOG_COLUMNS = ["dataset", "window", "rows", "run_id"]
SHARE_BACKFILL_START_YEAR = 1990
SHARE_NOTICE_REFRESH_DAYS = 60
DIVIDEND_START = date(2015, 12, 31)
DIVIDEND_REFRESH_DAYS = 550  # plans for a period go ex within about 18 months


def share_capital_windows(today: str | date, log: pd.DataFrame | None) -> list[tuple[str, str, str]]:
    """(date_field, start, end) windows to fetch.

    Yearly END_DATE windows backfill history once (the current year always
    refetches); a NOTICE_DATE window over the last 60 days catches changes
    announced late or ahead of their effective date.
    """
    today = as_date(today)
    done = _done(log, "share_capital")
    windows = []
    for year in range(SHARE_BACKFILL_START_YEAR, today.year + 2):
        window = ("END_DATE", f"{year}-01-01", f"{year + 1}-01-01")
        if year >= today.year or _key(window) not in done:
            windows.append(window)
    start = today - timedelta(days=SHARE_NOTICE_REFRESH_DAYS)
    windows.append(("NOTICE_DATE", start.isoformat(), (today + timedelta(days=1)).isoformat()))
    return windows


def dividend_report_dates(today: str | date, log: pd.DataFrame | None) -> list[str]:
    today = as_date(today)
    done = _done(log, "dividends")
    dates = []
    for period in quarter_ends(DIVIDEND_START, today):
        recent = (today - period).days <= DIVIDEND_REFRESH_DAYS
        if recent or dividend_window(period.isoformat()) not in done:
            dates.append(period.isoformat())
    return dates


def dividend_window(report_date: str) -> str:
    """Fetch-log key and raw file name of one dividend report period."""
    return f"report_{report_date}"


def quarter_ends(start: date, end: date) -> list[date]:
    periods, year = [], start.year
    while True:
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            period = date(year, month, day)
            if period > end:
                return periods
            if period >= start:
                periods.append(period)
        year += 1


def _key(window: Iterable[str]) -> str:
    return "_".join(window)


def _done(log: pd.DataFrame | None, dataset: str) -> set[str]:
    if log is None or log.empty:
        return set()
    return set(log.loc[log["dataset"] == dataset, "window"])


def merge_fetch_log(existing: pd.DataFrame | None, rows: list[dict]) -> pd.DataFrame:
    parts = [part for part in (existing, pd.DataFrame(rows, columns=FETCH_LOG_COLUMNS))
             if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=FETCH_LOG_COLUMNS)
    frame = pd.concat(parts, ignore_index=True)
    frame = frame.drop_duplicates(["dataset", "window"], keep="last")
    return frame.sort_values(["dataset", "window"], ignore_index=True)


def same_frame(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """Content equality of a fresh response and its stored raw copy."""
    if list(left.columns) != list(right.columns) or len(left) != len(right):
        return False
    return left.astype("string").reset_index(drop=True).equals(right.astype("string").reset_index(drop=True))
