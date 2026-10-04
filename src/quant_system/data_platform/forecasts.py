"""Earnings forecasts (业绩预告): Eastmoney datacenter RPT_PUBLIC_OP_NEWPREDICT.

One row per company, report period, forecast item (net profit, deducted net
profit, revenue, ...) and announcement: a revised forecast is a new
announcement, so earlier ones stay (each is what was known from its notice
date).  The position manager's quality grade reads the latest net-profit
forecast type known at a date (docs/design/position-manager.md §11.6).

Fetching mirrors the statements (financials.py): every report period from
FORECAST_START once, the latest SWEEP_PERIODS again every week, and a
NOTICE_DATE window over the last UPDATE_WINDOW_DAYS on every run.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .corporate import _a_share_rows, _numbers, _symbols, quarter_ends
from .normalization import _lineage, _to_date, as_date

SOURCE_FORECASTS = "eastmoney.datacenter.RPT_PUBLIC_OP_NEWPREDICT"
DATASET = "earnings_forecast"
FORECAST_START = date(2020, 3, 31)
UPDATE_WINDOW_DAYS = 45
SWEEP_PERIODS = 4
SWEEP_EVERY_DAYS = 7
KEY_COLUMNS = ["symbol", "report_date", "finance_code", "notice_date"]
COLUMNS = KEY_COLUMNS + ["finance_name", "predict_type", "amount_lower", "amount_upper", "change_lower",
                         "change_upper", "previous_amount", "content", "source", "ingested_at", "run_id",
                         "schema_version"]
NET_PROFIT = "004"  # PREDICT_FINANCE_CODE of 归属于上市公司股东的净利润


def normalize_forecasts(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    """Curated rows of one response (A shares with a notice date)."""
    if raw is None or raw.empty or "SECURITY_CODE" not in raw.columns:
        return pd.DataFrame(columns=COLUMNS)
    raw = raw[_a_share_rows(raw)]
    notice = _to_date(raw["NOTICE_DATE"]) if "NOTICE_DATE" in raw else pd.Series(None, index=raw.index)
    raw, notice = raw[notice.notna()], notice[notice.notna()]

    def text(column: str) -> pd.Series:
        return raw[column].astype("string") if column in raw.columns else pd.Series(None, index=raw.index,
                                                                                     dtype="string")

    frame = pd.DataFrame({
        "symbol": _symbols(raw["SECURITY_CODE"]).astype(str),
        "report_date": _to_date(raw["REPORT_DATE"]),
        "finance_code": text("PREDICT_FINANCE_CODE"),
        "notice_date": notice,
        "finance_name": text("PREDICT_FINANCE"),
        "predict_type": text("PREDICT_TYPE"),
        "amount_lower": _numbers(raw, "PREDICT_AMT_LOWER"),
        "amount_upper": _numbers(raw, "PREDICT_AMT_UPPER"),
        "change_lower": _numbers(raw, "ADD_AMP_LOWER"),
        "change_upper": _numbers(raw, "ADD_AMP_UPPER"),
        "previous_amount": _numbers(raw, "PREYEAR_SAME_PERIOD"),
        "content": text("PREDICT_CONTENT"),
    }, index=raw.index)
    frame = frame[frame["report_date"].notna()]
    frame = _lineage(frame, SOURCE_FORECASTS, run_id, ingested_at)
    return frame.drop_duplicates(KEY_COLUMNS, keep="last")[COLUMNS].reset_index(drop=True)


def merge_forecasts(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """Append-only by announcement; a row seen again keeps its latest content."""
    parts = [part.reindex(columns=COLUMNS) for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    merged = pd.concat(parts, ignore_index=True).drop_duplicates(KEY_COLUMNS, keep="last")
    return merged.sort_values(KEY_COLUMNS, ignore_index=True)


def forecast_windows(today: str | date, log: pd.DataFrame | None) -> list[tuple[str, str, str, str]]:
    """(name, date field, start, end) windows to fetch; names sort in the order the rebuild replays."""
    today = as_date(today)
    last_fetch: dict[str, date] = {}
    if log is not None and not log.empty:
        for row in log.itertuples(index=False):
            day = date(int(row.run_id[:4]), int(row.run_id[4:6]), int(row.run_id[6:8]))
            last_fetch[row.window] = max(last_fetch.get(row.window, day), day)
    # Forecasts of a period come out up to about a month after it ends, a little before as well.
    periods = quarter_ends(FORECAST_START, today + timedelta(days=100))
    recent = set(periods[-SWEEP_PERIODS:])
    windows = []
    for period in periods:
        name = f"period_{period.isoformat()}"
        seen = last_fetch.get(name)
        if seen is None or (period in recent and (today - seen).days >= SWEEP_EVERY_DAYS):
            windows.append((name, "REPORT_DATE", period.isoformat(), (period + timedelta(days=1)).isoformat()))
    start = (today - timedelta(days=UPDATE_WINDOW_DAYS)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    windows.append((f"update_{start}_{end}", "NOTICE_DATE", start, end))
    return windows


def latest_profit_forecast(frame: pd.DataFrame, symbol: str, as_of: date, max_age_days: int = 183
                           ) -> dict | None:
    """The newest net-profit forecast of ``symbol`` announced on or before ``as_of``
    (and not older than ``max_age_days``): its type, period and notice date."""
    if frame is None or frame.empty:
        return None
    rows = frame[(frame["symbol"] == symbol) & (frame["finance_code"] == NET_PROFIT)]
    notice = pd.to_datetime(rows["notice_date"]).dt.date
    rows = rows[(notice <= as_of) & (notice >= as_of - timedelta(days=max_age_days))]
    if rows.empty:
        return None
    row = rows.sort_values(["notice_date", "report_date"]).iloc[-1]
    return {"type": row["predict_type"], "report_date": row["report_date"], "notice_date": row["notice_date"]}
