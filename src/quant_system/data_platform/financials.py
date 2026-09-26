"""Financial statements (Eastmoney datacenter RPT_F10_FINANCE_*), versioned.

The vendor keeps one row per (company, period) holding the latest
(possibly restated) values, with NOTICE_DATE = first announcement and
UPDATE_DATE = last revision.  We store every distinct version we observe:
a later fetch whose curated values differ becomes version k+1.  History
fetched before this store existed has only the vendor's latest version,
labeled version 1 and usable from its first announcement (restatement
bias; ADR-004).  Versions observed later are usable from the session after
max(NOTICE_DATE, UPDATE_DATE).

Income and cash-flow values are year-to-date cumulative, as reported.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .normalization import _lineage, _to_date, as_date
from .corporate import A_SHARE_SUFFIXES, _numbers, _symbols, quarter_ends
from .symbols import is_a_share

STATEMENTS = ("income", "balance", "cashflow")
COMPANY_TYPES = ("G", "B", "S", "I")  # general, bank, securities, insurance
REPORT_NAMES = {"income": "INCOME", "balance": "BALANCE", "cashflow": "CASHFLOW"}
SOURCE_FINANCIALS = "eastmoney.datacenter.RPT_F10_FINANCE"

# canonical field -> vendor fields in order of preference (first non-null wins)
FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "income": {
        "revenue": ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME"),
        "operate_income": ("OPERATE_INCOME",),
        "operate_cost": ("OPERATE_COST",),
        "operate_profit": ("OPERATE_PROFIT",),
        "total_profit": ("TOTAL_PROFIT",),
        "income_tax": ("INCOME_TAX",),
        "net_profit": ("NETPROFIT",),
        "parent_net_profit": ("PARENT_NETPROFIT",),
        "deducted_parent_net_profit": ("DEDUCT_PARENT_NETPROFIT",),
        "sale_expense": ("SALE_EXPENSE",),
        "manage_expense": ("MANAGE_EXPENSE",),
        "research_expense": ("RESEARCH_EXPENSE",),
        "finance_expense": ("FINANCE_EXPENSE",),
        "basic_eps": ("BASIC_EPS",),
    },
    "balance": {
        "total_assets": ("TOTAL_ASSETS",),
        "total_liabilities": ("TOTAL_LIABILITIES",),
        "total_equity": ("TOTAL_EQUITY",),
        "parent_equity": ("TOTAL_PARENT_EQUITY",),
        "minority_equity": ("MINORITY_EQUITY",),
        "other_equity_instruments": ("OTHER_EQUITY_TOOL",),
        "preferred_shares": ("PREFERRED_SHARES",),
        "perpetual_bonds": ("PERPETUAL_BOND",),
        "share_capital": ("SHARE_CAPITAL",),
        "cash": ("MONETARYFUNDS",),
        "accounts_receivable": ("ACCOUNTS_RECE",),
        "inventory": ("INVENTORY",),
        "goodwill": ("GOODWILL",),
        "current_assets": ("TOTAL_CURRENT_ASSETS",),
        "current_liabilities": ("TOTAL_CURRENT_LIAB",),
        "short_loans": ("SHORT_LOAN",),
        "long_loans": ("LONG_LOAN",),
        "bonds_payable": ("BOND_PAYABLE",),
    },
    "cashflow": {
        "operating_cash_flow": ("NETCASH_OPERATE",),
        "investing_cash_flow": ("NETCASH_INVEST",),
        "financing_cash_flow": ("NETCASH_FINANCE",),
        "capex": ("CONSTRUCT_LONG_ASSET",),
        "depreciation": ("FA_IR_DEPR",),
        "amortization": ("IA_AMORTIZE",),
    },
}
# Financial companies appear both in the general (G) table and in their own
# (B/S/I) table with slightly different fields, so each table keeps its own
# version lineage; the derivation prefers the specific table (pit.py).
KEY_COLUMNS = ["symbol", "report_date", "company_type"]
META_COLUMNS = ["report_type", "notice_date", "update_date", "version", "content_hash", "first_seen_run"]
LINEAGE_COLUMNS = ["source", "ingested_at", "run_id", "schema_version"]


def statement_columns(statement: str) -> list[str]:
    return KEY_COLUMNS + META_COLUMNS + list(FIELDS[statement]) + LINEAGE_COLUMNS


def _content_hash(values: pd.DataFrame) -> pd.Series:
    def one(row: np.ndarray) -> str:
        payload = [None if not np.isfinite(v) else float(f"{v:.6g}") for v in row]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:20]

    return pd.Series([one(row) for row in values.to_numpy(dtype=np.float64)], index=values.index)


def normalize_financials(raw: pd.DataFrame, statement: str, company_type: str, run_id: str,
                         ingested_at: str) -> tuple[pd.DataFrame, dict[str, int]]:
    """Curated rows of one vendor report; returns (frame, drop counts)."""
    columns = statement_columns(statement)
    counts = {"rows": 0, "not_a_share": 0, "not_quarter_end": 0, "no_notice_date": 0}
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns), counts
    counts["rows"] = len(raw)
    symbols = _symbols(raw["SECURITY_CODE"])
    a_share = symbols.map(lambda s: isinstance(s, str) and is_a_share(s)).astype(bool)
    if "SECUCODE" in raw.columns:
        a_share &= raw["SECUCODE"].astype("string").str.split(".").str[-1].isin(A_SHARE_SUFFIXES).fillna(False)
    counts["not_a_share"] = int((~a_share).sum())
    raw, symbols = raw[a_share], symbols[a_share]
    report_dates = _to_date(raw["REPORT_DATE"])
    quarter_end = report_dates.map(lambda d: d is not None and (d.month, d.day) in ((3, 31), (6, 30), (9, 30),
                                                                                    (12, 31)))
    notice = _to_date(raw["NOTICE_DATE"]) if "NOTICE_DATE" in raw else pd.Series(None, index=raw.index)
    counts["not_quarter_end"] = int((~quarter_end).sum())
    counts["no_notice_date"] = int((quarter_end & notice.isna()).sum())
    keep = quarter_end & notice.notna()
    raw = raw[keep]
    frame = pd.DataFrame({
        "symbol": symbols[keep].astype(str),
        "report_date": report_dates[keep],
        "company_type": company_type,
        "report_type": raw.get("REPORT_TYPE", pd.Series(None, index=raw.index)).astype("string"),
        "notice_date": notice[keep],
        "update_date": _to_date(raw["UPDATE_DATE"]) if "UPDATE_DATE" in raw else None,
    }, index=raw.index)
    for name, candidates in FIELDS[statement].items():
        value = pd.Series(np.nan, index=raw.index)
        for candidate in candidates:
            value = value.fillna(_numbers(raw, candidate))
        frame[name] = value
    frame["content_hash"] = _content_hash(frame[list(FIELDS[statement])])
    frame["version"] = 0
    frame["first_seen_run"] = run_id
    frame = _lineage(frame, f"{SOURCE_FINANCIALS}.{company_type}{REPORT_NAMES[statement]}", run_id, ingested_at)
    # Duplicates inside one response: the most recently updated row wins.
    frame = frame.sort_values(["symbol", "report_date", "update_date"], kind="stable", na_position="first")
    frame = frame.drop_duplicates(KEY_COLUMNS, keep="last")
    return frame[columns].reset_index(drop=True), counts


def merge_financial_versions(existing: pd.DataFrame | None, incoming: pd.DataFrame,
                             statement: str) -> tuple[pd.DataFrame, int]:
    """Append incoming rows whose curated values differ from the latest
    stored version of the same (symbol, report_date, company_type).
    Returns (merged, new version count)."""
    columns = statement_columns(statement)
    if incoming is None or incoming.empty:
        base = existing if existing is not None else pd.DataFrame(columns=columns)
        return base.reindex(columns=columns), 0
    if existing is None or existing.empty:
        latest = pd.DataFrame(columns=KEY_COLUMNS + ["version", "content_hash"])
    else:
        latest = existing.sort_values("version").drop_duplicates(KEY_COLUMNS, keep="last")
    joined = incoming.drop(columns=["version"]).merge(
        latest[KEY_COLUMNS + ["version", "content_hash"]].rename(
            columns={"version": "latest_version", "content_hash": "latest_hash"}),
        on=KEY_COLUMNS, how="left")
    changed = joined["latest_hash"].isna() | (joined["latest_hash"] != joined["content_hash"])
    new = joined[changed].copy()
    new["version"] = pd.to_numeric(new["latest_version"], errors="coerce").fillna(0).astype(int) + 1
    new = new[columns]
    parts = [part for part in (existing, new) if part is not None and not part.empty]
    merged = pd.concat([part.reindex(columns=columns) for part in parts], ignore_index=True)
    merged = merged.sort_values(KEY_COLUMNS + ["version"], ignore_index=True)
    return merged, int(len(new))


# ---------------------------------------------------------------------------
# Fetch planning
# ---------------------------------------------------------------------------

FINANCIALS_START = date(2016, 3, 31)
UPDATE_WINDOW_DAYS = 45
SWEEP_PERIODS = 6        # recent periods re-fetched in full ...
SWEEP_EVERY_DAYS = 7     # ... once a week, in case UPDATE_DATE misses a revision


def financial_windows(today: str | date, log: pd.DataFrame | None) -> list[tuple[str, str, str, str, str]]:
    """(name, company_type, field, start, end) windows for one statement set.

    Every period is fetched once; the latest ``SWEEP_PERIODS`` periods are
    re-fetched weekly; an UPDATE_DATE window over the last 45 days runs on
    every call and catches new filings and revisions.
    """
    today = as_date(today)
    last_fetch: dict[str, date] = {}
    if log is not None and not log.empty:
        for row in log.itertuples(index=False):
            day = date(int(row.run_id[:4]), int(row.run_id[4:6]), int(row.run_id[6:8]))
            last_fetch[row.window] = max(last_fetch.get(row.window, day), day)
    periods = quarter_ends(FINANCIALS_START, today)
    recent = set(periods[-SWEEP_PERIODS:])
    windows = []
    for ctype in COMPANY_TYPES:
        for period in periods:
            end = (period + timedelta(days=1)).isoformat()
            name = f"{ctype}_period_{period.isoformat()}"
            seen = last_fetch.get(name)
            if seen is None or (period in recent and (today - seen).days >= SWEEP_EVERY_DAYS):
                windows.append((name, ctype, "REPORT_DATE", period.isoformat(), end))
        start = (today - timedelta(days=UPDATE_WINDOW_DAYS)).isoformat()
        end = (today + timedelta(days=1)).isoformat()
        windows.append((f"{ctype}_update_{start}_{end}", ctype, "UPDATE_DATE", start, end))
    return windows
