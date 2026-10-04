"""Quality grades of the stocks in users' libraries (docs/design/position-manager.md §11.6).

Inputs as known at the latest session (ADR-004 point-in-time rules):
financial metrics from the versioned statements (fundamentals/pit.py), now
and as known a year earlier; the market value from the unadjusted close and
the latest total shares announced; the latest net-profit forecast.  Grades
are stored in ``pm_quality``; they order lists and trigger nothing.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data_platform.forecasts import latest_profit_forecast
from ..fundamentals.pit import FIN_COLUMNS, INPUTS, STATEMENTS, derive_store
from ..position.quality import grade
from .db.base import utc_now
from .db.models import PmItem, PmQuality
from .market import MarketQueries

log = logging.getLogger(__name__)
HISTORY_DAYS = 3 * 366  # sessions the statement replay spans (older statements count from the first)


def _frame(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> pd.DataFrame:
    try:
        return con.execute(sql, params).fetchdf()
    except duckdb.CatalogException:  # a catalog without that dataset
        return pd.DataFrame()


def _ratio(now: float, before: float) -> float:
    return now / before - 1 if math.isfinite(now) and math.isfinite(before) and before > 0 else math.nan


def quality_values(con: duckdb.DuckDBPyConnection, symbols: list[str]) -> tuple[date | None, dict[str, dict]]:
    """(as-of session, symbol -> {values..., forecast, periods}) for ``grade``."""
    calendar = _frame(con, "SELECT DISTINCT trade_date FROM trading_calendar WHERE is_open "
                           "AND trade_date <= (SELECT max(trade_date) FROM daily_bars) ORDER BY trade_date", [])
    if calendar.empty or not symbols:
        return None, {}
    sessions = tuple(pd.to_datetime(calendar["trade_date"]).dt.date)
    as_of = sessions[-1]
    sessions = tuple(d for d in sessions if d >= as_of - timedelta(days=HISTORY_DAYS))
    placeholders = ", ".join("?" for _ in symbols)
    tables = {}
    for statement in STATEMENTS:
        columns = ", ".join(FIN_COLUMNS + list(INPUTS[statement]))
        tables[statement] = _frame(con, f"SELECT {columns} FROM fin_{statement} WHERE symbol IN ({placeholders}) "
                                        "ORDER BY symbol, report_date, version", symbols)
    store = derive_store({k: v for k, v in tables.items() if not v.empty}, sessions, np.array(symbols))
    last = len(sessions) - 1
    year_ago = max(0, int(np.searchsorted(np.array(sessions, dtype="datetime64[D]"),
                                          np.datetime64(as_of - timedelta(days=365)), side="right")) - 1)
    rows = np.array([last, year_ago])

    def metric(name: str) -> np.ndarray:
        return store.asof(name, rows)  # [2, N]: now, a year ago

    ni, revenue, gross, book = metric("ni_ttm"), metric("revenue_ttm"), metric("gross_profit_ttm"), metric("avg_book")
    closes = _frame(con, f"SELECT symbol, arg_max(close, trade_date) AS close FROM daily_bars "
                         f"WHERE symbol IN ({placeholders}) AND trade_date <= ? GROUP BY symbol", [*symbols, as_of])
    shares = _frame(con, f"SELECT symbol, arg_max(total_shares, change_date) AS shares FROM share_capital "
                         f"WHERE symbol IN ({placeholders}) AND change_date <= ? "
                         f"AND (notice_date IS NULL OR notice_date <= ?) GROUP BY symbol", [*symbols, as_of, as_of])
    forecasts = _frame(con, f"SELECT * FROM earnings_forecast WHERE symbol IN ({placeholders})", symbols)
    close_of = dict(zip(closes.get("symbol", []), closes.get("close", [])))
    shares_of = dict(zip(shares.get("symbol", []), shares.get("shares", [])))
    out: dict[str, dict] = {}
    for j, symbol in enumerate(symbols):
        value = close_of.get(symbol, math.nan) * shares_of.get(symbol, math.nan)
        margin_now = gross[0, j] / revenue[0, j] if revenue[0, j] else math.nan
        margin_before = gross[1, j] / revenue[1, j] if revenue[1, j] else math.nan
        forecast = latest_profit_forecast(forecasts, symbol, as_of) if not forecasts.empty else None
        out[symbol] = {
            "pe": value / ni[0, j] if math.isfinite(value) and math.isfinite(ni[0, j]) and ni[0, j] != 0 else math.nan,
            "profit_growth": _ratio(ni[0, j], ni[1, j]),
            "revenue_growth": _ratio(revenue[0, j], revenue[1, j]),
            "roe": ni[0, j] / book[0, j] if math.isfinite(book[0, j]) and book[0, j] > 0 else math.nan,
            "margin_change": (margin_now - margin_before) * 100,
            "forecast": forecast["type"] if forecast else None,
            "forecast_period": forecast["report_date"] if forecast else None,
        }
    return as_of, out


def refresh_quality(session: Session, market: MarketQueries, symbols: list[str] | None = None) -> int:
    """Grade the given stocks (default: every stock in a library); returns how many were stored."""
    if symbols is None:
        symbols = sorted(set(session.scalars(select(PmItem.symbol).where(PmItem.kind == "stock",
                                                                         PmItem.archived_at.is_(None)))))
    if not symbols or not market.available():
        return 0
    with duckdb.connect(str(market.database), read_only=True) as con:
        as_of, values = quality_values(con, symbols)
    if as_of is None:
        return 0
    for symbol, inputs in values.items():
        result = grade(inputs, inputs["forecast"])
        result["dims"]["forecast"]["period"] = inputs["forecast_period"]
        row = session.get(PmQuality, symbol) or PmQuality(symbol=symbol)
        row.as_of, row.grade, row.score, row.computed_at = as_of, result["grade"], result["score"], utc_now()
        row.dims = _json_safe(result["dims"])
        session.add(row)
    session.flush()
    return len(values)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, pd.Timestamp)):
        return str(value)[:10]
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value
