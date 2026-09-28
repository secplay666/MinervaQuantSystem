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


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict(orient="records")]


class MarketQueries:
    def __init__(self, database: Path, rules_path: Path) -> None:
        self.database = database
        self.rules = MarketRules.load(rules_path)
        self._overview: dict[tuple[date, float], dict[str, Any]] = {}

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

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{q.strip()}%"
        frame = self._query("SELECT symbol, name, board, delist_date FROM security_master "
                            "WHERE symbol LIKE ? OR name LIKE ? ORDER BY delist_date IS NOT NULL, symbol LIMIT ?",
                            [f"{q.strip()}%", pattern, limit])
        return records(frame)

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
             limit: int = 500) -> list[dict[str, Any]]:
        o, h, l, c = ADJUST_COLUMNS[adjust]
        end = end or self.latest_session()
        start = start or (end - timedelta(days=int(limit * 1.6)))
        frame = self._query(
            f"SELECT trade_date, {o} AS open, {h} AS high, {l} AS low, {c} AS close, volume_shares AS volume, "
            f"turnover_cny AS amount, pct_change FROM daily_bars_adjusted WHERE symbol = ? "
            f"AND trade_date BETWEEN ? AND ? ORDER BY trade_date DESC LIMIT ?", [symbol, start, end, limit])
        return records(frame.iloc[::-1])

    def index_bars(self, symbol: str, start: date | None, end: date | None, limit: int = 500) -> list[dict]:
        end = end or self.latest_session()
        start = start or (end - timedelta(days=int(limit * 1.6)))
        frame = self._query("SELECT trade_date, open, high, low, close, volume_shares AS volume, "
                            "turnover_cny AS amount FROM index_bars WHERE symbol = ? AND trade_date BETWEEN ? AND ? "
                            "ORDER BY trade_date DESC LIMIT ?", [symbol, start, end, limit])
        return records(frame.iloc[::-1])

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
