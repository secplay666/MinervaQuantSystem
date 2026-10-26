"""Shareholder counts (股东户数) and the top-ten holders' combined stake: Eastmoney datacenter RPT_F10_EH_HOLDERNUM.

One row per company and count date (``end_date``): the counts of the periodic reports (quarter ends) and the ones
some companies disclose in between (investor Q&A, some every ten days).  The top-ten holders' and the top-ten float
holders' combined stakes come with the periodic reports only.  A count is known from its notice date.

Backfilled a year of notice dates at a time from COUNT_START, then by NOTICE_DATE over the last COUNT_WINDOW_DAYS
on every run.  A count delivered again replaces the stored one (the vendor corrects in place, rarely).

Read by the 股东户数 factor (features/holders.py, a candidate under ADR-009), the chart's holder panes and the
position manager's 大涨后股东户数大增 reminder.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .corporate import _a_share_rows, _numbers, _symbols
from .normalization import _lineage, _to_date

SOURCE_SHAREHOLDER_COUNTS = "eastmoney.datacenter.RPT_F10_EH_HOLDERNUM"
COUNT_REPORT = "RPT_F10_EH_HOLDERNUM"
COUNT_SORT = "SECUCODE,END_DATE"  # unique per row
COUNT_START = 2005
COUNT_WINDOW_DAYS = 30
COUNT_COLUMNS = ["symbol", "end_date", "notice_date", "holders", "holders_total", "avg_free_shares", "top10_pct",
                 "top10_free_pct", "approximate", "source", "ingested_at", "run_id", "schema_version"]


def normalize_shareholder_counts(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    """A-share rows with a count date, a notice date on or after it, and a count or a stake.

    ``holders`` counts the A-share holders: the vendor's A count, else its total for a company without B shares
    (the total then adds at most the few registered H-share holders)."""
    if raw is None or raw.empty or "SECURITY_CODE" not in raw.columns:
        return pd.DataFrame(columns=COUNT_COLUMNS)
    raw = raw[raw["SECURITY_CODE"].notna()]
    raw = raw[_a_share_rows(raw)]
    total = _numbers(raw, "HOLDER_TOTAL_NUM")
    no_b_shares = raw["B_MARK"].astype("string").ne("1").fillna(True) if "B_MARK" in raw.columns else True
    frame = pd.DataFrame({
        "symbol": _symbols(raw["SECURITY_CODE"]).astype(str),
        "end_date": _to_date(raw["END_DATE"]),
        "notice_date": _to_date(raw["NOTICE_DATE"]) if "NOTICE_DATE" in raw.columns else None,
        "holders": _numbers(raw, "HOLDER_A_NUM").fillna(total.where(no_b_shares)),
        "holders_total": total,
        "avg_free_shares": _numbers(raw, "AVG_FREE_SHARES"),
        "top10_pct": _numbers(raw, "HOLD_RATIO_TOTAL"),  # % of total shares
        "top10_free_pct": _numbers(raw, "FREEHOLD_RATIO_TOTAL"),  # % of float shares
        "approximate": raw["IS_APPROXIMATION"].astype("string").eq("1").fillna(False).astype(bool)
        if "IS_APPROXIMATION" in raw.columns else False,
    }, index=raw.index)
    known = frame["end_date"].notna() & frame["notice_date"].notna()
    frame = frame[known & (frame["holders"].notna() | frame["top10_pct"].notna())]
    frame = frame[pd.to_datetime(frame["notice_date"]) >= pd.to_datetime(frame["end_date"])]
    frame = _lineage(frame, SOURCE_SHAREHOLDER_COUNTS, run_id, ingested_at)
    return frame.drop_duplicates(["symbol", "end_date"], keep="last").reindex(columns=COUNT_COLUMNS) \
        .reset_index(drop=True)


def merge_shareholder_counts(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """One row per company and count date; a count delivered again replaces the stored one."""
    parts = [p.reindex(columns=COUNT_COLUMNS) for p in (existing, incoming) if p is not None and not p.empty]
    if not parts:
        return pd.DataFrame(columns=COUNT_COLUMNS)
    frame = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol", "end_date"], keep="last")
    return frame.sort_values(["symbol", "end_date"], ignore_index=True)


def count_windows(existing: pd.DataFrame | None, today: date) -> list[tuple[str, str]]:
    """(name, datacenter filter): a year of notice dates at a time from COUNT_START the first time, then recent ones."""
    if existing is None or existing.empty:
        return [(f"notice_{year}", f"(NOTICE_DATE>='{year}-01-01')(NOTICE_DATE<'{year + 1}-01-01')")
                for year in range(COUNT_START, today.year + 1)]
    start = min(pd.to_datetime(existing["notice_date"]).max().date(), today) - timedelta(days=COUNT_WINDOW_DAYS)
    return [(f"notice_{start:%Y%m%d}", f"(NOTICE_DATE>='{start:%Y-%m-%d}')")]
