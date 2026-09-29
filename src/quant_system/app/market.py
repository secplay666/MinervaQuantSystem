"""Read-only market queries for the API, straight from ``market.duckdb``.

Each call opens a short read-only connection (the daily catalog swap is
never blocked, ADR-005).  The market overview of a session is computed once
and cached until the database file changes.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..domain.rules import MarketRules

INDICES = ("sh000001", "sz399001", "sz399006", "sh000300", "sh000905", "sh000688")
RISK_STATUS = {"ST": "risk_warning", "*ST": "risk_warning", "DELISTING": "delisting_period"}
ADJUST_COLUMNS = {"none": ("open", "high", "low", "close"),
                  "qfq": ("qfq_open", "qfq_high", "qfq_low", "qfq_close"),
                  "hfq": ("hfq_open", "hfq_high", "hfq_low", "hfq_close")}


def _clean(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    return value


PERIOD_DAYS = {"day": 1, "week": 5, "month": 23}  # most sessions per bar, to size the daily query


def aggregate_bars(frame: pd.DataFrame, period: str, with_turnover_rate: bool = False) -> pd.DataFrame:
    """Weekly (ISO week) or monthly bars from daily rows, dated by the last session."""
    dates = pd.to_datetime(frame["trade_date"])
    iso = dates.dt.isocalendar()
    key = iso["year"] * 100 + iso["week"] if period == "week" else dates.dt.year * 100 + dates.dt.month
    rules = {"trade_date": "last", "open": "first", "high": "max", "low": "min", "close": "last",
             "volume": "sum", "amount": "sum", "hfq_close": "last"}
    if with_turnover_rate:
        rules["turnover_rate"] = "sum"
    out = frame.groupby(key.values, sort=True).agg(rules).reset_index(drop=True)
    out["pct_change"] = (out["hfq_close"] / out["hfq_close"].shift(1) - 1) * 100
    previous = out["close"].shift(1)
    out["amplitude"] = (out["high"] - out["low"]) / previous * 100
    return out


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


class MarketQueries:
    def __init__(self, database: Path, rules_path: Path) -> None:
        self.database = database
        self.rules = MarketRules.load(rules_path)
        self._overview: dict[tuple[date, float], dict[str, Any]] = {}
        self._indices: tuple[float, list[dict[str, Any]]] | None = None

    def _query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        with duckdb.connect(str(self.database), read_only=True) as con:
            return con.execute(sql, params or []).fetchdf()

    def available(self) -> bool:
        return self.database.is_file()

    def latest_session(self) -> date | None:
        frame = self._query("SELECT max(trade_date) AS d FROM trading_calendar")
        value = frame["d"].iloc[0]
        return None if pd.isna(value) else pd.Timestamp(value).date()

    def sessions(self, end: date, count: int) -> list[date]:
        frame = self._query("SELECT trade_date FROM trading_calendar WHERE trade_date <= ? "
                            "ORDER BY trade_date DESC LIMIT ?", [end, count])
        return sorted(pd.to_datetime(frame["trade_date"]).dt.date)

    def known_symbols(self) -> set[str]:
        return set(self._query("SELECT symbol FROM security_master")["symbol"].astype(str))

    def names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        frame = self._query(f"SELECT symbol, name FROM security_master WHERE symbol IN "
                            f"({', '.join('?' for _ in symbols)})", list(symbols))
        return dict(zip(frame["symbol"].astype(str), frame["name"].astype(str)))

    def latest_closes(self, symbols: list[str]) -> dict[str, float]:
        """Last close (CNY) of each symbol on or before the latest session."""
        if not symbols:
            return {}
        frame = self._query(
            f"SELECT symbol, close FROM daily_bars WHERE symbol IN ({', '.join('?' for _ in symbols)}) "
            "QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1", list(symbols))
        return {str(s): float(c) for s, c in zip(frame["symbol"], frame["close"])}

    def security(self, symbol: str) -> dict[str, Any] | None:
        frame = self._query("SELECT symbol, name, exchange, board, list_date, delist_date, status "
                            "FROM security_master WHERE symbol = ?", [symbol])
        return records(frame)[0] if len(frame) else None

    def indices(self) -> list[dict[str, Any]]:
        """Indices with daily bars (price and total-return), for search and the chart."""
        key = self.database.stat().st_mtime
        if self._indices is None or self._indices[0] != key:
            try:
                frame = self._query("SELECT symbol, any_value(name) AS name, max(trade_date) AS latest "
                                    "FROM index_bars GROUP BY symbol ORDER BY symbol")
            except duckdb.CatalogException:  # a database without indices
                frame = pd.DataFrame(columns=["symbol", "name", "latest"])
            self._indices = (key, records(frame))
        return self._indices[1]

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """Indices first (by code or name, e.g. 上证 or 000300), then stocks."""
        text = q.strip()
        needle = text.lower()
        indices = [{"symbol": i["symbol"], "name": i["name"], "board": "INDEX", "delist_date": None}
                   for i in self.indices() if needle in i["symbol"].lower() or text in (i["name"] or "")]
        pattern = f"%{text}%"
        frame = self._query("SELECT symbol, name, board, delist_date FROM security_master "
                            "WHERE symbol LIKE ? OR name LIKE ? ORDER BY delist_date IS NOT NULL, symbol LIMIT ?",
                            [f"{text}%", pattern, limit])
        return (indices + records(frame))[:limit]

    def industries(self, symbols: list[str]) -> dict[str, str]:
        """{symbol: SW level-1 industry name} at the latest session."""
        if not symbols:
            return {}
        day = self.latest_session()
        try:
            frame = self._query(
                f"SELECT symbol, l1_name FROM industry_sw WHERE symbol IN ({', '.join('?' for _ in symbols)}) "
                "AND start_date <= ? AND (end_date IS NULL OR end_date >= ?) ORDER BY start_date",
                [*symbols, day, day])
        except duckdb.CatalogException:  # a database without the classification (tests, partial rebuilds)
            return {}
        return dict(zip(frame["symbol"].astype(str), frame["l1_name"].astype(str)))

    def instrument(self, symbol: str) -> dict[str, Any] | None:
        info = self.security(symbol)
        if info is None:
            return None
        day = self.latest_session()
        industry = self._query("SELECT l1_code, l1_name, l2_name, l3_name FROM industry_sw WHERE symbol = ? "
                               "AND start_date <= ? AND (end_date IS NULL OR end_date >= ?) "
                               "ORDER BY start_date DESC LIMIT 1", [symbol, day, day])
        risk = self._query("SELECT status, start_date FROM risk_warning_intervals WHERE symbol = ? "
                           "AND start_date <= ? AND (end_date IS NULL OR end_date >= ?) "
                           "ORDER BY start_date DESC LIMIT 1", [symbol, day, day])
        info["industry"] = records(industry)[0] if len(industry) else None
        info["risk_status"] = str(risk["status"].iloc[0]) if len(risk) else "normal"
        return info

    def bars(self, symbol: str, start: date | None, end: date | None, adjust: str = "qfq",
             limit: int = 500, period: str = "day") -> list[dict[str, Any]]:
        """The last ``limit`` bars up to ``end`` (daily, weekly or monthly).
        Weekly and monthly bars are built from the daily ones and dated by
        their last session; their change is measured on hfq closes, so it is
        right across ex-dates whatever the display adjustment."""
        o, h, l, c = ADJUST_COLUMNS[adjust]
        end = end or self.latest_session()
        days = limit * PERIOD_DAYS[period]
        start = start or (end - timedelta(days=int(days * 1.6) + 10))
        frame = self._query(
            f"SELECT trade_date, {o} AS open, {h} AS high, {l} AS low, {c} AS close, volume_shares AS volume, "
            f"turnover_cny AS amount, pct_change, turnover_rate_pct AS turnover_rate, amplitude_pct AS amplitude, "
            f"hfq_close FROM daily_bars_adjusted WHERE symbol = ? AND trade_date BETWEEN ? AND ? "
            f"ORDER BY trade_date DESC LIMIT ?", [symbol, start, end, days + 2 * PERIOD_DAYS[period]])
        frame = frame.iloc[::-1].reset_index(drop=True)
        if period != "day":
            frame = aggregate_bars(frame, period, with_turnover_rate=True)
        # Two spare periods were read, so the oldest (maybe cut) week or month falls outside ``limit``.
        return records(frame.drop(columns=["hfq_close"]).tail(limit))

    def index_bars(self, symbol: str, start: date | None, end: date | None, limit: int = 500,
                   period: str = "day") -> list[dict]:
        end = end or self.latest_session()
        days = limit * PERIOD_DAYS[period]
        start = start or (end - timedelta(days=int(days * 1.6) + 10))
        frame = self._query("SELECT trade_date, open, high, low, close, volume_shares AS volume, "
                            "turnover_cny AS amount, close AS hfq_close FROM index_bars WHERE symbol = ? "
                            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date DESC LIMIT ?",
                            [symbol, start, end, days + 2 * PERIOD_DAYS[period]])
        frame = frame.iloc[::-1].reset_index(drop=True)
        if period != "day":
            frame = aggregate_bars(frame, period)
        return records(frame.drop(columns=["hfq_close"]).tail(limit))

    def marks(self, symbol: str) -> dict[str, list[dict[str, Any]]]:
        """Events to mark on the chart: implemented dividends and splits (ex-dates),
        periodic reports (first notice), risk-warning intervals, suspensions."""
        def query(sql: str) -> list[dict[str, Any]]:
            try:
                return records(self._query(sql, [symbol]))
            except duckdb.CatalogException:  # a database without that table
                return []

        return {
            "dividends": query("SELECT ex_date, report_date, plan_profile, cash_per_10, bonus_per_10, transfer_per_10 "
                               "FROM dividends WHERE symbol = ? AND ex_date IS NOT NULL ORDER BY ex_date"),
            "reports": query("SELECT report_date, MIN(notice_date) AS notice_date FROM fin_income WHERE symbol = ? "
                             "AND notice_date IS NOT NULL GROUP BY report_date ORDER BY notice_date"),
            "risk": query("SELECT status, start_date, end_date, start_title FROM risk_warning_intervals "
                          "WHERE symbol = ? ORDER BY start_date"),
            "suspensions": query("SELECT suspend_start, suspend_end, reason FROM suspension_events WHERE symbol = ? "
                                 "AND suspend_start IS NOT NULL ORDER BY suspend_start"),
        }

    def fundamentals(self, symbol: str, periods: int = 8) -> list[dict[str, Any]]:
        frame = self._query(
            "SELECT report_date, notice_date, revenue, operate_income, net_profit, parent_net_profit, "
            "deducted_parent_net_profit, basic_eps FROM fin_income WHERE symbol = ? "
            "QUALIFY row_number() OVER (PARTITION BY report_date ORDER BY version DESC) = 1 "
            "ORDER BY report_date DESC LIMIT ?", [symbol, periods])
        return records(frame)

    # -- overview ---------------------------------------------------------------------------

    def overview(self, day: date | None = None) -> dict[str, Any]:
        day = day or self.latest_session()
        key = (day, self.database.stat().st_mtime)
        if key not in self._overview:
            self._overview = {key: self._compute_overview(day)}
        return self._overview[key]

    def _compute_overview(self, day: date) -> dict[str, Any]:
        previous = self.sessions(day, 2)
        prev = previous[0] if len(previous) == 2 else None
        indices = self._query(
            "SELECT symbol, name, trade_date, close FROM index_bars WHERE trade_date IN (?, ?) "
            f"AND symbol IN ({', '.join('?' for _ in INDICES)})", [day, prev or day, *INDICES])
        index_rows = []
        for symbol in INDICES:
            rows = indices[indices["symbol"] == symbol].sort_values("trade_date")
            if rows.empty:
                continue
            close = float(rows["close"].iloc[-1])
            before = float(rows["close"].iloc[0]) if len(rows) == 2 else None
            index_rows.append({"symbol": symbol, "name": rows["name"].iloc[-1], "close": close,
                               "change_pct": (close / before - 1) if before else None})
        bars = self._query(
            "SELECT b.symbol, b.trade_date, b.close, b.hfq_factor, b.turnover_cny, m.board, m.list_date "
            "FROM daily_bars_adjusted b JOIN security_master m USING (symbol) WHERE b.trade_date IN (?, ?)",
            [day, prev or day])
        risk = self._query("SELECT symbol, status FROM risk_warning_intervals WHERE start_date <= ? "
                           "AND (end_date IS NULL OR end_date >= ?)", [day, day])
        industry = self._query("SELECT symbol, l1_name FROM industry_sw WHERE start_date <= ? "
                               "AND (end_date IS NULL OR end_date >= ?)", [day, day])
        today = bars[pd.to_datetime(bars["trade_date"]).dt.date == day].set_index("symbol")
        before = bars[pd.to_datetime(bars["trade_date"]).dt.date == prev].set_index("symbol") if prev else None
        status = dict(zip(risk["symbol"], risk["status"]))
        up = down = limit_up = limit_down = flat = 0
        changes = {}
        for symbol, row in today.iterrows():
            if before is None or symbol not in before.index:
                continue
            ref = float(before.at[symbol, "close"]) * float(before.at[symbol, "hfq_factor"]) / float(row["hfq_factor"])
            change = float(row["close"]) / ref - 1
            changes[symbol] = change
            up += change > 0
            down += change < 0
            flat += change == 0
            listed = pd.Timestamp(row["list_date"]).date() if pd.notna(row["list_date"]) else None
            if listed is not None and (day - listed).days < 10:
                continue  # IPO window without a price band
            ratio = self.rules.limit_ratio(str(row["board"]), RISK_STATUS.get(status.get(symbol), "normal"), day)
            up_fen, down_fen = self.rules.limit_prices(int(round(ref * 100)), ratio)
            close_fen = int(round(float(row["close"]) * 100))
            limit_up += close_fen >= up_fen
            limit_down += close_fen <= down_fen
        sectors = pd.DataFrame({"symbol": list(changes), "change": list(changes.values())}).merge(
            industry, on="symbol", how="inner")
        by_sector = (sectors.groupby("l1_name")["change"].agg(["mean", "count"]).reset_index()
                     .sort_values("mean", ascending=False))
        return {
            "session": day.isoformat(), "previous_session": prev.isoformat() if prev else None,
            "indices": index_rows,
            "breadth": {"up": int(up), "down": int(down), "flat": int(flat), "limit_up": int(limit_up),
                        "limit_down": int(limit_down), "traded": int(len(today)),
                        "amount_cny": float(today["turnover_cny"].sum())},
            "industries": [{"name": r.l1_name, "change_pct": float(r.mean), "count": int(r.count)}
                           for r in by_sector.itertuples()],
        }
