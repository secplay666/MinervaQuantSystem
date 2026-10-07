"""Buybacks (回购) and holder increases/decreases (股东增减持), Eastmoney datacenter.

* ``buybacks``: RPTA_WEB_GETHGLIST_NEW, one row per buyback plan in its latest
  state (the vendor updates a plan's progress and executed amount in place):
  merged on ``plan_id``, the newest ``update_date`` wins.  Fetched whole once,
  then by UPDATE_DATE over the last BUYBACK_WINDOW_DAYS on every run.
* ``holder_changes``: RPT_SHARE_HOLDER_INCREASE, one row per disclosed change
  of a holder (append-only, deduplicated on ``change_key``).  Backfilled a
  year at a time from HOLDER_START, then by NOTICE_DATE over the last
  HOLDER_WINDOW_DAYS on every run.

Both are information for the 回购增持 page and the position manager's tags,
not signals: research/buyback/buyback_study.py found no edge worth trading.
Pages are sorted on a unique key; sorting on a date alone lets rows of one
day move between pages.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pandas as pd

from .corporate import _a_share_rows, _numbers, _symbols
from .normalization import _lineage, _to_date

SOURCE_BUYBACKS = "eastmoney.datacenter.RPTA_WEB_GETHGLIST_NEW"
SOURCE_HOLDER_CHANGES = "eastmoney.datacenter.RPT_SHARE_HOLDER_INCREASE"
BUYBACK_REPORT = "RPTA_WEB_GETHGLIST_NEW"
HOLDER_REPORT = "RPT_SHARE_HOLDER_INCREASE"
BUYBACK_SORT = "REPURCODE"
HOLDER_SORT = "NOTICE_DATE,SECURITY_CODE,HOLDER_NAME,START_DATE,END_DATE,CHANGE_NUM"
BUYBACK_WINDOW_DAYS = 10
HOLDER_START = 2016
HOLDER_WINDOW_DAYS = 20

# The vendor's progress codes, named from what the plans in each state carry (no published list).
PROGRESS = {"001": "董事会通过", "002": "股东会通过", "003": "未实施", "004": "实施中", "005": "已停止",
            "006": "已完成", "007": "待股东会", "008": "其他"}
KINDS = {"cancel": "注销", "incentive": "股权激励/员工持股", "other": "其他"}

BUYBACK_COLUMNS = ["plan_id", "symbol", "name", "notice_date", "latest_notice_date", "update_date", "progress", "kind",
                   "purpose", "amount_lower", "amount_upper", "price_cap", "shares_lower", "shares_upper", "start_date",
                   "end_date", "done_amount", "done_shares", "done_avg_price", "finish_date",
                   "source", "ingested_at", "run_id", "schema_version"]
HOLDER_COLUMNS = ["change_key", "symbol", "name", "holder", "direction", "change_shares", "change_pct_total",
                  "change_pct_free", "hold_pct_after", "avg_price", "channel", "start_date", "end_date",
                  "notice_date", "source", "ingested_at", "run_id", "schema_version"]


def _text(raw: pd.DataFrame, column: str) -> pd.Series:
    return raw[column].astype("string") if column in raw.columns else pd.Series(None, index=raw.index, dtype="string")


def _date(raw: pd.DataFrame, column: str) -> pd.Series:
    return _to_date(raw[column]) if column in raw.columns else pd.Series(None, index=raw.index, dtype="object")


def buyback_kind(purpose: pd.Series) -> pd.Series:
    """cancel (注销 / 减少注册资本 named anywhere) > incentive (股权激励 / 员工持股) > other."""
    text = purpose.fillna("")
    kind = pd.Series("other", index=purpose.index)
    kind[text.str.contains("股权激励|员工持股")] = "incentive"
    kind[text.str.contains("注销|减少注册资本")] = "cancel"
    return kind


def normalize_buybacks(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty or "REPURCODE" not in raw.columns:
        return pd.DataFrame(columns=BUYBACK_COLUMNS)
    raw = raw[raw["DIM_SCODE"].notna() & raw["REPURCODE"].notna()]
    raw = raw[_a_share_rows(raw, code="DIM_SCODE")]
    frame = pd.DataFrame({
        "plan_id": raw["REPURCODE"].astype(str),
        "symbol": _symbols(raw["DIM_SCODE"]).astype(str),
        "name": _text(raw, "SECURITYSHORTNAME"),
        "notice_date": _date(raw, "DIM_DATE"),
        "latest_notice_date": _date(raw, "NOTICEDATE"),
        "update_date": _date(raw, "UPDATEDATE"),
        "progress": _text(raw, "REPURPROGRESS"),
        "purpose": _text(raw, "REPUROBJECTIVE"),
        "amount_lower": _numbers(raw, "REPURAMOUNTLOWER"),
        "amount_upper": _numbers(raw, "REPURAMOUNTLIMIT"),
        "price_cap": _numbers(raw, "REPURPRICECAP"),
        "shares_lower": _numbers(raw, "REPURNUMLOWER"),
        "shares_upper": _numbers(raw, "REPURNUMCAP"),
        "start_date": _date(raw, "REPURSTARTDATE"),
        "end_date": _date(raw, "REPURENDDATE"),
        "done_amount": _numbers(raw, "REPURAMOUNT"),
        "done_shares": _numbers(raw, "REPURNUM"),
        "finish_date": _date(raw, "FINISHDATE"),
    })
    # Not the vendor's ZJJG: that is the middle of the price range paid, or the price cap before any purchase.
    bought = (frame["done_amount"] > 0) & (frame["done_shares"] > 0)
    frame["done_avg_price"] = (frame["done_amount"] / frame["done_shares"]).where(bought)
    frame["kind"] = buyback_kind(frame["purpose"])
    frame = frame[frame["notice_date"].notna()]
    # About 3% of plans carry no latest notice date: their latest notice is the first one.
    frame["latest_notice_date"] = frame["latest_notice_date"].where(frame["latest_notice_date"].notna(),
                                                                    frame["notice_date"])
    return _lineage(frame, SOURCE_BUYBACKS, run_id, ingested_at).reindex(columns=BUYBACK_COLUMNS)


def merge_buybacks(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """One row per plan, its latest state (newest update date, then the newer run)."""
    parts = [p for p in (existing, incoming) if p is not None and not p.empty]
    if not parts:
        return pd.DataFrame(columns=BUYBACK_COLUMNS)
    frame = pd.concat([p.reindex(columns=BUYBACK_COLUMNS) for p in parts], ignore_index=True)
    frame["_order"] = range(len(frame))
    frame = frame.sort_values(["update_date", "_order"], na_position="first", kind="stable")
    frame = frame.drop_duplicates("plan_id", keep="last").drop(columns="_order")
    return frame.sort_values(["notice_date", "plan_id"], ignore_index=True)


def _change_key(frame: pd.DataFrame) -> pd.Series:
    parts = frame[["symbol", "holder", "direction", "start_date", "end_date", "change_shares", "notice_date"]].astype(str)
    return parts.agg("|".join, axis=1).map(lambda text: hashlib.sha1(text.encode("utf-8")).hexdigest()[:20])


def normalize_holder_changes(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty or "SECURITY_CODE" not in raw.columns:
        return pd.DataFrame(columns=HOLDER_COLUMNS)
    raw = raw[raw["SECURITY_CODE"].notna() & raw["NOTICE_DATE"].notna()]
    raw = raw[_a_share_rows(raw)]
    frame = pd.DataFrame({
        "symbol": _symbols(raw["SECURITY_CODE"]).astype(str),
        "name": _text(raw, "SECURITY_NAME_ABBR"),
        "holder": _text(raw, "HOLDER_NAME"),
        "direction": _text(raw, "DIRECTION"),
        "change_shares": _numbers(raw, "CHANGE_NUM") * 1e4,  # the vendor counts in 10,000 shares
        "change_pct_total": _numbers(raw, "AFTER_CHANGE_RATE"),  # % of total shares (despite the name)
        "change_pct_free": _numbers(raw, "CHANGE_FREE_RATIO"),
        "hold_pct_after": _numbers(raw, "HOLD_RATIO"),
        "avg_price": _numbers(raw, "TRADE_AVERAGE_PRICE"),
        "channel": _text(raw, "MARKET"),
        "start_date": _date(raw, "START_DATE"),
        "end_date": _date(raw, "END_DATE"),
        "notice_date": _date(raw, "NOTICE_DATE"),
    })
    frame = frame[frame["notice_date"].notna()]
    frame["change_key"] = _change_key(frame) if len(frame) else pd.Series(dtype="string")
    return _lineage(frame, SOURCE_HOLDER_CHANGES, run_id, ingested_at).reindex(columns=HOLDER_COLUMNS)


def merge_holder_changes(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """Append-only: a change already stored keeps its first record."""
    parts = [p for p in (existing, incoming) if p is not None and not p.empty]
    if not parts:
        return pd.DataFrame(columns=HOLDER_COLUMNS)
    frame = pd.concat([p.reindex(columns=HOLDER_COLUMNS) for p in parts], ignore_index=True)
    return frame.drop_duplicates("change_key", keep="first").sort_values(["notice_date", "change_key"],
                                                                         ignore_index=True)


def buyback_windows(existing: pd.DataFrame | None, today: date) -> list[tuple[str, str]]:
    """(name, datacenter filter): the whole table the first time, then recent updates."""
    if existing is None or existing.empty or existing["update_date"].isna().all():
        return [("all", "")]
    start = min(pd.to_datetime(existing["update_date"]).max().date(), today) - timedelta(days=BUYBACK_WINDOW_DAYS)
    return [(f"update_{start:%Y%m%d}", f"(UPDATEDATE>='{start:%Y-%m-%d}')")]


def holder_windows(existing: pd.DataFrame | None, today: date) -> list[tuple[str, str]]:
    """(name, datacenter filter): a year at a time from HOLDER_START the first time, then recent notices."""
    if existing is None or existing.empty:
        return [(f"notice_{year}", f"(NOTICE_DATE>='{year}-01-01')(NOTICE_DATE<'{year + 1}-01-01')")
                for year in range(HOLDER_START, today.year + 1)]
    start = min(pd.to_datetime(existing["notice_date"]).max().date(), today) - timedelta(days=HOLDER_WINDOW_DAYS)
    return [(f"notice_{start:%Y%m%d}", f"(NOTICE_DATE>='{start:%Y-%m-%d}')")]
