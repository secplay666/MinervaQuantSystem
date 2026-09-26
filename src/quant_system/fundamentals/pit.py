"""Point-in-time fundamentals from versioned financial statements (ADR-004).

Each statement row becomes usable at the close of the first session after
its availability date:

* version 1: the first announcement (NOTICE_DATE);
* version k >= 2 (observed by this store later): max(NOTICE_DATE, UPDATE_DATE).

Per symbol, rows are replayed in availability order; after every batch the
derived metrics are recomputed from what is known then:

* TTM flows  = YTD(P) + FY(Y-1) - YTD(P, Y-1); Q4 uses the annual report.
  P is the latest known period whose TTM parent net profit can be formed
  (a late filing of an older period never displaces a newer one).
* balance items at the latest known balance-sheet period, averages with the
  same period a year earlier (the current value when that is missing);
* single-quarter year-on-year growth from YTD differences.

Metrics older than MAX_AGE_DAYS after their period end are treated as
missing (a company that stops reporting drops out).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

MAX_AGE_DAYS = 365
SPECIFIC_TYPES = ("B", "S", "I")  # bank, securities, insurance tables beat the general (G) table
STATEMENTS = ("income", "balance", "cashflow")
INPUTS = {
    "income": ("revenue", "operate_cost", "parent_net_profit", "deducted_parent_net_profit"),
    "balance": ("parent_equity", "other_equity_instruments", "preferred_shares", "perpetual_bonds",
                "total_assets", "total_liabilities"),
    "cashflow": ("operating_cash_flow",),
}
OUTPUTS = ("ni_ttm", "dni_ttm", "revenue_ttm", "gross_profit_ttm", "ocf_ttm", "book", "total_assets",
           "total_liabilities", "avg_book", "avg_assets", "ni_sq_yoy", "rev_sq_yoy", "dni_sq_yoy")
PERIOD_OF = {"ni_ttm": 0, "dni_ttm": 0, "revenue_ttm": 0, "gross_profit_ttm": 0, "ni_sq_yoy": 0, "rev_sq_yoy": 0,
             "dni_sq_yoy": 0, "book": 1, "total_assets": 1, "total_liabilities": 1, "avg_book": 1,
             "avg_assets": 1, "ocf_ttm": 2}
NAN = float("nan")


def _year_ago(period: date) -> date:
    return date(period.year - 1, period.month, period.day)


def _previous_quarter(period: date) -> date:
    return {3: date(period.year - 1, 12, 31), 6: date(period.year, 3, 31), 9: date(period.year, 6, 30),
            12: date(period.year, 9, 30)}[period.month]


def _get(state: dict[date, dict[str, float]], period: date, field: str) -> float:
    values = state.get(period)
    return NAN if values is None else values.get(field, NAN)


def ttm(state: dict[date, dict[str, float]], period: date, field: str) -> float:
    current = _get(state, period, field)
    if period.month == 12:
        return current
    return current + _get(state, date(period.year - 1, 12, 31), field) - _get(state, _year_ago(period), field)


def single_quarter(state: dict[date, dict[str, float]], period: date, field: str) -> float:
    current = _get(state, period, field)
    return current if period.month == 3 else current - _get(state, _previous_quarter(period), field)


def _growth(state: dict[date, dict[str, float]], period: date, field: str, scale: float) -> float:
    now, base = single_quarter(state, period, field), single_quarter(state, _year_ago(period), field)
    if not (math.isfinite(now) and math.isfinite(base)):
        return NAN
    floor = 1e-4 * scale if math.isfinite(scale) and scale > 0 else 0.0
    return (now - base) / abs(base) if abs(base) > floor else NAN


def _book(values: dict[str, float] | None) -> float:
    if not values:
        return NAN
    equity = values.get("parent_equity", NAN)
    other = values.get("other_equity_instruments", NAN)
    if not math.isfinite(other):
        other = sum(v for v in (values.get("preferred_shares", NAN), values.get("perpetual_bonds", NAN))
                    if math.isfinite(v))
    return equity - other


def snapshot(state: dict[str, dict[date, dict[str, float]]]) -> tuple[list[float], list[date | None]]:
    """Derived metrics (OUTPUTS order) and the (income, balance, cashflow)
    periods they come from."""
    income, balance, cashflow = state["income"], state["balance"], state["cashflow"]
    out = dict.fromkeys(OUTPUTS, NAN)
    periods: list[date | None] = [None, None, None]
    for period in sorted(income, reverse=True)[:4]:
        ni = ttm(income, period, "parent_net_profit")
        if math.isfinite(ni):
            periods[0] = period
            revenue = ttm(income, period, "revenue")
            out.update(ni_ttm=ni, dni_ttm=ttm(income, period, "deducted_parent_net_profit"), revenue_ttm=revenue,
                       gross_profit_ttm=revenue - ttm(income, period, "operate_cost"))
            break
    if balance:
        latest = max(balance)
        periods[1] = latest
        current, year_ago = balance[latest], balance.get(_year_ago(latest))
        book, book_ago = _book(current), _book(year_ago)
        assets = current.get("total_assets", NAN)
        assets_ago = year_ago.get("total_assets", NAN) if year_ago else NAN
        out.update(book=book, total_assets=assets, total_liabilities=current.get("total_liabilities", NAN),
                   avg_book=(book + book_ago) / 2 if math.isfinite(book_ago) else book,
                   avg_assets=(assets + assets_ago) / 2 if math.isfinite(assets_ago) else assets)
    if income:
        latest = max(income)
        if periods[0] is None:
            periods[0] = latest
        scale = out["total_assets"]
        out.update(ni_sq_yoy=_growth(income, latest, "parent_net_profit", scale),
                   rev_sq_yoy=_growth(income, latest, "revenue", scale),
                   dni_sq_yoy=_growth(income, latest, "deducted_parent_net_profit", scale))
    for period in sorted(cashflow, reverse=True)[:4]:
        ocf = ttm(cashflow, period, "operating_cash_flow")
        if math.isfinite(ocf):
            out["ocf_ttm"], periods[2] = ocf, period
            break
    return [out[name] for name in OUTPUTS], periods


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@dataclass
class FundamentalStore:
    """Derived metrics as step functions of the session index, per symbol."""

    sessions: tuple[date, ...]
    n_symbols: int
    cols: np.ndarray      # int64 [E], sorted with eff
    eff: np.ndarray       # int64 [E]
    values: np.ndarray    # float64 [E, len(OUTPUTS)]
    periods: np.ndarray   # datetime64[D] [E, 3]
    options: dict[str, Any]

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for array in (self.cols, self.eff, self.values, self.periods.astype("int64")):
            digest.update(np.ascontiguousarray(array).tobytes())
        digest.update(repr(sorted(self.options.items())).encode())
        return digest.hexdigest()

    def asof(self, field: str, rows: np.ndarray) -> np.ndarray:
        """Value known at the close of each requested session row [R, N]."""
        rows = np.asarray(rows, dtype=np.int64)
        k = OUTPUTS.index(field)
        T = len(self.sessions)
        keys = self.cols * T + self.eff
        query = np.arange(self.n_symbols, dtype=np.int64)[None, :] * T + rows[:, None]
        position = np.searchsorted(keys, query, side="right") - 1
        safe = np.maximum(position, 0)
        valid = (position >= 0) & (self.cols[safe] == np.arange(self.n_symbols)[None, :])
        value = np.where(valid, self.values[safe, k], np.nan)
        period = self.periods[safe, PERIOD_OF[field]]
        session_days = np.array(self.sessions, dtype="datetime64[D]")[rows][:, None]
        fresh = ~np.isnat(period) & ((session_days - period).astype(np.int64) <= MAX_AGE_DAYS)
        return np.where(valid & fresh, value, np.nan)


def availability(frame: pd.DataFrame) -> pd.Series:
    notice = pd.to_datetime(frame["notice_date"])
    update = pd.to_datetime(frame["update_date"])
    later = frame["version"] >= 2
    return notice.where(~later, np.maximum(notice, update.fillna(notice)))


def prefer_specific_tables(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop general-table rows of companies reported in a bank, securities
    or insurance table (the same statements, with the right fields)."""
    if "company_type" not in frame.columns:
        return frame
    specific = set(frame.loc[frame["company_type"].isin(SPECIFIC_TYPES), "symbol"])
    return frame[~((frame["company_type"] == "G") & frame["symbol"].isin(specific))]


def derive_store(tables: dict[str, pd.DataFrame], sessions: tuple[date, ...], symbols: np.ndarray,
                 extra_lag_sessions: int = 0) -> FundamentalStore:
    """``extra_lag_sessions`` delays every statement by that many sessions
    (timeliness sensitivity; 0 in normal use)."""
    session_days = np.array(sessions, dtype="datetime64[D]")
    columns = {str(symbol): j for j, symbol in enumerate(symbols)}
    parts = []
    for statement in STATEMENTS:
        frame = tables.get(statement)
        if frame is None or frame.empty:
            continue
        frame = prefer_specific_tables(frame[frame["symbol"].astype(str).isin(columns)])
        available = availability(frame).to_numpy(dtype="datetime64[D]")
        eff = np.searchsorted(session_days, available, side="right") + extra_lag_sessions
        part = pd.DataFrame({"col": frame["symbol"].astype(str).map(columns).to_numpy(), "eff": eff,
                             "statement": statement, "period": pd.to_datetime(frame["report_date"]).dt.date.to_numpy(),
                             "version": frame["version"].to_numpy()})
        for name in INPUTS[statement]:
            part[name] = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=np.float64)
        parts.append(part[part["eff"] < len(sessions)])
    cols, effs, values, periods = [], [], [], []
    if parts:
        rows = pd.concat(parts, ignore_index=True).sort_values(["col", "eff", "version"], kind="stable")
        for col, group in rows.groupby("col", sort=True):
            state: dict[str, dict[date, dict[str, float]]] = {s: {} for s in STATEMENTS}
            previous = None
            records = group.to_dict("records")
            i = 0
            while i < len(records):
                eff = records[i]["eff"]
                while i < len(records) and records[i]["eff"] == eff:
                    record = records[i]
                    state[record["statement"]][record["period"]] = {
                        name: record[name] for name in INPUTS[record["statement"]]}
                    i += 1
                value, period = snapshot(state)
                key = (tuple(value), tuple(period))
                if key == previous:
                    continue  # nothing changed at this session
                previous = key
                cols.append(col)
                effs.append(eff)
                values.append(value)
                periods.append([np.datetime64(p, "D") if p else np.datetime64("NaT", "D") for p in period])
    return FundamentalStore(
        sessions=tuple(sessions), n_symbols=len(symbols),
        cols=np.asarray(cols, dtype=np.int64), eff=np.asarray(effs, dtype=np.int64),
        values=np.asarray(values, dtype=np.float64).reshape(-1, len(OUTPUTS)),
        periods=np.asarray(periods, dtype="datetime64[D]").reshape(-1, 3),
        options={"extra_lag_sessions": extra_lag_sessions})


FIN_COLUMNS = ["symbol", "report_date", "company_type", "notice_date", "update_date", "version"]


def read_statement_tables(source: str, path: Path) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    if source == "duckdb":
        with duckdb.connect(str(path), read_only=True) as con:
            existing = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
            for statement in STATEMENTS:
                if f"fin_{statement}" in existing:
                    columns = ", ".join(FIN_COLUMNS + list(INPUTS[statement]))
                    tables[statement] = con.execute(
                        f"SELECT {columns} FROM fin_{statement} ORDER BY symbol, report_date, version").fetchdf()
    elif source == "parquet_dir":
        for statement in STATEMENTS:
            file = Path(path) / f"fin_{statement}.parquet"
            if file.exists():
                tables[statement] = pd.read_parquet(file)
    return tables


def load_fundamentals(source: str, path: Path, market: Any, extra_lag_sessions: int = 0
                      ) -> FundamentalStore | None:
    tables = read_statement_tables(source, path)
    if not tables:
        return None
    return derive_store(tables, market.sessions, market.symbols, extra_lag_sessions)
