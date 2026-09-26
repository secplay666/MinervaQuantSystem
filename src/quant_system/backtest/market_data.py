"""Market data panels (sessions x symbols) for the backtest.

Only base tables are read — never the ``instruments`` or
``daily_bars_adjusted`` views, which carry current-state or re-anchored
(non point-in-time) information.  Risk-warning intervals without a start
date (``current_name_only``) are dropped: applying today's ST status to
history would be look-ahead.  ``delist_date`` is kept for the engine's
lifecycle events only and is never exposed to strategies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

RISK_NORMAL, RISK_WARNING, RISK_DELISTING = 0, 1, 2
RISK_CODES = {"ST": RISK_WARNING, "*ST": RISK_WARNING, "DELISTING": RISK_DELISTING}
RISK_NAMES = {RISK_NORMAL: "normal", RISK_WARNING: "risk_warning", RISK_DELISTING: "delisting_period"}
BEFORE_DATA = -1_000_000  # listing index for securities listed before the first session

FRAME_FILES = ("calendar", "bars", "factors", "master", "risk", "indices")


def _to_days(values: pd.Series) -> np.ndarray:
    return pd.to_datetime(values).to_numpy(dtype="datetime64[D]")


def _date_or_none(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


@dataclass
class MarketData:
    sessions: tuple[date, ...]
    symbols: np.ndarray
    board: np.ndarray
    exchange: np.ndarray
    list_date: list[date | None]
    list_index: np.ndarray
    delist_date: list[date | None]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    has_bar: np.ndarray
    hfq: np.ndarray
    last_bar: np.ndarray
    bar_count: np.ndarray
    risk: np.ndarray
    risk_known: np.ndarray
    indices: dict[str, np.ndarray]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def symbol_index(self, symbol: str) -> int:
        position = int(np.searchsorted(self.symbols, symbol))
        if position >= len(self.symbols) or self.symbols[position] != symbol:
            raise KeyError(symbol)
        return position

    def session_index(self, day: date) -> int:
        position = int(np.searchsorted(self._session_days, np.datetime64(day, "D")))
        if position >= len(self.sessions) or self.sessions[position] != day:
            raise KeyError(day)
        return position

    @property
    def _session_days(self) -> np.ndarray:
        return np.array(self.sessions, dtype="datetime64[D]")

    def fingerprint(self) -> str:
        """Content hash of every panel the engine reads."""
        digest = hashlib.sha256()
        digest.update(json.dumps([str(day) for day in self.sessions]).encode())
        digest.update("\n".join(self.symbols.tolist()).encode())
        digest.update("\n".join(self.board.tolist()).encode())
        digest.update(json.dumps([str(day) for day in self.list_date]).encode())
        digest.update(json.dumps([str(day) for day in self.delist_date]).encode())
        for name in ("open", "high", "low", "close", "volume", "turnover", "has_bar", "hfq", "risk"):
            digest.update(np.ascontiguousarray(getattr(self, name)).tobytes())
        for name in sorted(self.indices):
            digest.update(name.encode())
            digest.update(np.ascontiguousarray(self.indices[name]).tobytes())
        return digest.hexdigest()

    # -- construction -----------------------------------------------------------

    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame], metadata: dict[str, Any] | None = None) -> "MarketData":
        """Build panels from base-table frames (see ``FRAME_FILES``)."""
        calendar = sorted(pd.to_datetime(frames["calendar"]["trade_date"]).dt.date.unique())
        sessions = tuple(calendar)
        session_days = np.array(sessions, dtype="datetime64[D]")
        n_sessions = len(sessions)

        master = frames["master"].copy()
        bars = frames["bars"]
        symbols = np.array(sorted(set(master["symbol"]) & set(bars["symbol"])), dtype=object)
        master = master.set_index("symbol").loc[symbols.tolist()]
        n_symbols = len(symbols)

        def index_of(day: date | None) -> int:
            if day is None:
                return BEFORE_DATA
            if day < sessions[0]:
                return BEFORE_DATA
            return int(np.searchsorted(session_days, np.datetime64(day, "D")))

        list_date = [_date_or_none(value) for value in master["list_date"]]
        delist_date = [_date_or_none(value) for value in master["delist_date"]]

        shape = (n_sessions, n_symbols)
        panels = {
            "open": np.zeros(shape, dtype=np.int32),
            "high": np.zeros(shape, dtype=np.int32),
            "low": np.zeros(shape, dtype=np.int32),
            "close": np.zeros(shape, dtype=np.int32),
            "volume": np.zeros(shape, dtype=np.float64),
            "turnover": np.zeros(shape, dtype=np.float64),
        }
        has_bar = np.zeros(shape, dtype=bool)
        bars = bars[bars["symbol"].isin(symbols)]
        rows = np.searchsorted(session_days, _to_days(bars["trade_date"]))
        on_calendar = (rows < n_sessions) & (session_days[np.minimum(rows, n_sessions - 1)] == _to_days(bars["trade_date"]))
        if not on_calendar.all():
            raise ValueError(f"{int((~on_calendar).sum())} bars fall outside the trading calendar")
        columns = np.searchsorted(symbols, bars["symbol"].to_numpy(dtype=object))
        for name in ("open", "high", "low", "close"):
            panels[name][rows, columns] = np.rint(bars[name].to_numpy(dtype=np.float64) * 100).astype(np.int32)
        panels["volume"][rows, columns] = bars["volume_shares"].to_numpy(dtype=np.float64)
        panels["turnover"][rows, columns] = bars["turnover_cny"].fillna(0).to_numpy(dtype=np.float64)
        has_bar[rows, columns] = True

        hfq = np.full(shape, np.nan)
        factors = frames["factors"]
        factors = factors[factors["symbol"].isin(symbols)].sort_values(["symbol", "effective_date"], kind="stable")
        for symbol, group in factors.groupby("symbol", sort=False):
            column = int(np.searchsorted(symbols, symbol))
            effective = _to_days(group["effective_date"])
            position = np.searchsorted(effective, session_days, side="right") - 1
            values = group["hfq_factor"].to_numpy(dtype=np.float64)
            hfq[:, column] = np.where(position >= 0, values[np.maximum(position, 0)], np.nan)

        session_numbers = np.arange(n_sessions, dtype=np.int32)[:, None]
        last_bar = np.maximum.accumulate(np.where(has_bar, session_numbers, -1), axis=0).astype(np.int32)
        bar_count = np.cumsum(has_bar, axis=0, dtype=np.int32)

        risk = np.zeros(shape, dtype=np.int8)
        risk_frame = frames["risk"]
        risk_frame = risk_frame[
            risk_frame["symbol"].isin(symbols) & risk_frame["start_date"].notna()
            & (risk_frame["method"] != "current_name_only")
        ]
        for row in risk_frame.itertuples(index=False):
            column = int(np.searchsorted(symbols, row.symbol))
            start = max(index_of(_date_or_none(row.start_date)), 0)
            end_day = _date_or_none(row.end_date)
            end = n_sessions if end_day is None else index_of(end_day)
            end = max(end, 0)
            if start < end:
                risk[start:end, column] = RISK_CODES.get(row.status, RISK_NORMAL)
        dated = set(risk_frame["symbol"])
        exchange = master["exchange"].to_numpy(dtype=object)
        # SZSE publishes complete dated name changes, so a SZSE symbol without
        # intervals was never risk-warned.  SSE/BSE histories rebuilt from
        # exchange bulletins (ADR-004) are complete in the same sense.
        sources = set(risk_frame["source"]) if "source" in risk_frame.columns else set()
        complete = {"SZSE"} | {code for code, source in (("SSE", "sse_bulletin"), ("BSE", "bse_announcement"))
                               if source in sources}
        risk_known = np.array([ex in complete or sym in dated for sym, ex in zip(symbols, exchange)], dtype=bool)

        indices: dict[str, np.ndarray] = {}
        index_frame = frames["indices"]
        for symbol, group in index_frame.groupby("symbol"):
            series = np.full(n_sessions, np.nan)
            positions = np.searchsorted(session_days, _to_days(group["trade_date"]))
            valid = (positions < n_sessions)
            valid &= session_days[np.minimum(positions, n_sessions - 1)] == _to_days(group["trade_date"])
            series[positions[valid]] = group["close"].to_numpy(dtype=np.float64)[valid]
            indices[str(symbol)] = series

        data = cls(
            sessions=sessions,
            symbols=symbols,
            board=master["board"].to_numpy(dtype=object),
            exchange=exchange,
            list_date=list_date,
            list_index=np.array([index_of(day) for day in list_date], dtype=np.int64),
            delist_date=delist_date,
            has_bar=has_bar,
            hfq=hfq,
            last_bar=last_bar,
            bar_count=bar_count,
            risk=risk,
            risk_known=risk_known,
            indices=indices,
            metadata=dict(metadata or {}),
            **panels,
        )
        for name in ("open", "high", "low", "close", "volume", "turnover", "has_bar", "hfq", "last_bar",
                     "bar_count", "risk", "risk_known", "list_index"):
            getattr(data, name).setflags(write=False)
        return data


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

QUERIES = {
    "calendar": "SELECT trade_date FROM trading_calendar ORDER BY trade_date",
    "bars": """SELECT symbol, trade_date, open, high, low, close, volume_shares, turnover_cny
               FROM daily_bars ORDER BY symbol, trade_date""",
    "factors": "SELECT symbol, effective_date, hfq_factor FROM adjustment_factors ORDER BY symbol, effective_date",
    "master": "SELECT symbol, board, exchange, list_date, delist_date FROM security_master ORDER BY symbol",
    "risk": """SELECT symbol, status, start_date, end_date, method, source FROM risk_warning_intervals
               ORDER BY symbol, start_date""",
    "indices": "SELECT symbol, trade_date, close FROM index_bars ORDER BY symbol, trade_date",
}


def read_frames_from_duckdb(database: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Read base tables; the connection is closed before returning so the
    daily catalog swap is never blocked (ADR-005)."""
    with duckdb.connect(str(database), read_only=True) as con:
        frames = {name: con.execute(sql).fetchdf() for name, sql in QUERIES.items()}
        latest = con.execute(
            "SELECT run_id, data_version FROM ingestion_runs WHERE status = 'complete' "
            "AND data_version IS NOT NULL ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    metadata = {"source": str(database), "catalog_run_id": latest[0] if latest else None,
                "data_version": latest[1] if latest else None}
    return frames, metadata


def load_market_data(source: str, path: Path) -> MarketData:
    if source == "duckdb":
        frames, metadata = read_frames_from_duckdb(path)
    elif source == "parquet_dir":
        frames = {name: pd.read_parquet(path / f"{name}.parquet") for name in FRAME_FILES}
        source_file = path / "SOURCE.json"
        metadata = json.loads(source_file.read_text(encoding="utf-8")) if source_file.exists() else {}
        metadata["source"] = str(path)
    else:
        raise ValueError(f"unknown market data source {source!r}")
    return MarketData.from_frames(frames, metadata)
