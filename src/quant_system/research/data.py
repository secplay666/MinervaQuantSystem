"""Research data: market panels plus point-in-time reference panels.

Kept separate from ``MarketData`` so the momentum backtest (and its golden
fingerprint) does not depend on the stage-3 datasets.  Every panel is
aligned with ``market.sessions`` x ``market.symbols`` and each row ``t``
only reflects information usable at the close of session ``t``
(ADR-004):

* shares      max(first session >= change date, first session > notice date)
* industry    first session >= the Shenwan 计入日期
* dividends   the ex-dividend session (implemented distributions only)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..backtest.market_data import MarketData, load_market_data

UNCLASSIFIED = "000000"
DIVIDEND_TTM_DAYS = 365

RESEARCH_QUERIES = {
    "shares": """SELECT symbol, change_date, notice_date, total_shares, float_a_shares, record_key
                 FROM share_capital ORDER BY symbol, change_date, record_key""",
    "industry": """SELECT symbol, start_date, end_date, l1_code, l1_name FROM industry_sw
                   ORDER BY symbol, start_date""",
    "dividends": """SELECT symbol, report_date, ex_date, record_date, cash_per_10, total_shares FROM dividends
                    WHERE ex_date IS NOT NULL ORDER BY symbol, ex_date, report_date""",
}
RESEARCH_FILES = tuple(RESEARCH_QUERIES)


def _days(values: pd.Series) -> np.ndarray:
    return pd.to_datetime(values).to_numpy(dtype="datetime64[D]")


@dataclass
class ResearchData:
    market: MarketData
    total_shares: np.ndarray      # float64 [T, N]; NaN = unknown
    float_shares: np.ndarray      # float64 [T, N]; listed (tradable) A shares
    industry: np.ndarray          # int16 [T, N]; index into industry_codes, -1 = unclassified
    industry_codes: tuple[str, ...]
    industry_names: tuple[str, ...]
    dividend_ttm_cny: np.ndarray  # float64 [T, N]; cash paid with ex-date in the last 365 days
    fundamentals: Any = None      # fundamentals.pit.FundamentalStore (stage 3, P3)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sessions(self) -> tuple[date, ...]:
        return self.market.sessions

    @property
    def symbols(self) -> np.ndarray:
        return self.market.symbols

    def close_yuan(self) -> np.ndarray:
        """Close of the last bar on or before each session (suspended names
        keep their last close), in yuan; NaN before the first bar."""
        market = self.market
        rows = np.maximum(market.last_bar, 0)
        close = np.take_along_axis(market.close, rows, axis=0).astype(np.float64) / 100.0
        return np.where(market.last_bar >= 0, close, np.nan)

    def market_cap(self, kind: str = "total") -> np.ndarray:
        shares = {"total": self.total_shares, "float": self.float_shares}[kind]
        return self.close_yuan() * shares

    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.market.fingerprint().encode())
        for name in ("total_shares", "float_shares", "industry", "dividend_ttm_cny"):
            digest.update(np.ascontiguousarray(getattr(self, name)).tobytes())
        digest.update(json.dumps([self.industry_codes, self.industry_names], ensure_ascii=False).encode())
        if self.fundamentals is not None:
            digest.update(self.fundamentals.fingerprint().encode())
        return digest.hexdigest()

    # -- construction -----------------------------------------------------------

    @classmethod
    def from_frames(cls, market: MarketData, frames: dict[str, pd.DataFrame],
                    metadata: dict[str, Any] | None = None) -> "ResearchData":
        session_days = np.array(market.sessions, dtype="datetime64[D]")
        shape = market.shape
        columns = {str(symbol): j for j, symbol in enumerate(market.symbols)}

        shares = frames.get("shares")
        total = np.full(shape, np.nan)
        floating = np.full(shape, np.nan)
        if shares is not None and not shares.empty:
            events = _share_events(shares, session_days, columns)
            total = _step_panel(events, "total_shares", shape)
            floating = _step_panel(events, "float_a_shares", shape)

        industry = np.full(shape, -1, dtype=np.int16)
        codes: tuple[str, ...] = ()
        names: tuple[str, ...] = ()
        intervals = frames.get("industry")
        if intervals is not None and not intervals.empty:
            industry, codes, names = _industry_panel(intervals, session_days, columns, shape)

        dividends = frames.get("dividends")
        dividend_ttm = np.zeros(shape)
        if dividends is not None and not dividends.empty:
            dividend_ttm = _dividend_ttm(dividends, market.sessions, session_days, columns, shape, total)

        data = cls(market=market, total_shares=total, float_shares=floating, industry=industry,
                   industry_codes=codes, industry_names=names, dividend_ttm_cny=dividend_ttm,
                   metadata=dict(metadata or {}))
        for name in ("total_shares", "float_shares", "industry", "dividend_ttm_cny"):
            getattr(data, name).setflags(write=False)
        return data


def _share_events(shares: pd.DataFrame, session_days: np.ndarray, columns: dict[str, int]) -> pd.DataFrame:
    frame = shares[shares["symbol"].astype(str).isin(columns)].copy()
    change = _days(frame["change_date"])
    first_on_or_after = np.searchsorted(session_days, change, side="left")
    notice = pd.to_datetime(frame["notice_date"]).to_numpy(dtype="datetime64[D]")
    after_notice = np.where(np.isnat(notice), 0, np.searchsorted(session_days, notice, side="right"))
    frame["eff"] = np.maximum(first_on_or_after, after_notice)
    frame["col"] = frame["symbol"].astype(str).map(columns)
    frame["change"] = change
    return frame[frame["eff"] < len(session_days)].sort_values(["col", "eff", "change", "record_key"],
                                                               kind="stable")


def _step_panel(events: pd.DataFrame, value: str, shape: tuple[int, int]) -> np.ndarray:
    """Forward-filled value of the latest event effective on or before each
    session (the last event of a session wins)."""
    panel = np.full(shape, np.nan)
    sessions = np.arange(shape[0])
    for col, group in events.groupby("col", sort=False):
        eff = group["eff"].to_numpy()
        values = group[value].to_numpy(dtype=np.float64)
        position = np.searchsorted(eff, sessions, side="right") - 1
        panel[:, int(col)] = np.where(position >= 0, values[np.maximum(position, 0)], np.nan)
    return panel


def _industry_panel(intervals: pd.DataFrame, session_days: np.ndarray, columns: dict[str, int],
                    shape: tuple[int, int]) -> tuple[np.ndarray, tuple[str, ...], tuple[str, ...]]:
    frame = intervals[intervals["symbol"].astype(str).isin(columns)]
    classified = frame[frame["l1_code"] != UNCLASSIFIED]
    names = classified.drop_duplicates("l1_code").set_index("l1_code")["l1_name"]
    codes = tuple(sorted(names.index))
    lookup = {code: k for k, code in enumerate(codes)}
    panel = np.full(shape, -1, dtype=np.int16)
    start = np.searchsorted(session_days, _days(frame["start_date"]), side="left")
    end_days = pd.to_datetime(frame["end_date"]).to_numpy(dtype="datetime64[D]")
    end = np.where(np.isnat(end_days), shape[0], np.searchsorted(session_days, end_days, side="left"))
    for row, s, e in zip(frame.itertuples(index=False), start, end):
        if s < e:
            panel[s:e, columns[str(row.symbol)]] = lookup.get(row.l1_code, -1)
    return panel, codes, tuple(str(names[code]) for code in codes)


def _dividend_ttm(dividends: pd.DataFrame, sessions: tuple[date, ...], session_days: np.ndarray,
                  columns: dict[str, int], shape: tuple[int, int], total_shares: np.ndarray) -> np.ndarray:
    """Cash distributed (CNY) with an ex-date in the trailing 365 days.

    Amount = cash per share x shares at the ex session (the vendor's plan
    share count when the panel has none), so later splits do not rescale it.
    """
    frame = dividends[dividends["symbol"].astype(str).isin(columns) & (dividends["cash_per_10"] > 0)]
    ex = np.searchsorted(session_days, _days(frame["ex_date"]), side="left")
    keep = ex < shape[0]
    frame, ex = frame[keep], ex[keep]
    cols = frame["symbol"].astype(str).map(columns).to_numpy()
    shares = total_shares[ex, cols] if len(ex) else np.array([])
    shares = np.where(np.isfinite(shares), shares, frame["total_shares"].to_numpy(dtype=np.float64))
    amounts = np.nan_to_num(frame["cash_per_10"].to_numpy(dtype=np.float64) / 10.0 * shares)
    paid = np.zeros(shape)
    np.add.at(paid, (ex, cols), amounts)
    cumulative = np.cumsum(paid, axis=0)
    # Row index of the last session at least 365 days before each session.
    horizon = np.array([day - timedelta(days=DIVIDEND_TTM_DAYS) for day in sessions], dtype="datetime64[D]")
    lag = np.searchsorted(session_days, horizon, side="right") - 1
    before = np.where((lag >= 0)[:, None], cumulative[np.maximum(lag, 0)], 0.0)
    return cumulative - before


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def read_research_frames(database: Path) -> dict[str, pd.DataFrame]:
    with duckdb.connect(str(database), read_only=True) as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        needed = {"shares": "share_capital", "industry": "industry_sw", "dividends": "dividends"}
        return {name: con.execute(sql).fetchdf() for name, sql in RESEARCH_QUERIES.items()
                if needed[name] in tables}


def load_research_data(source: str, path: Path, market: MarketData | None = None) -> ResearchData:
    """Load research panels (and the market panels unless given)."""
    market = market or load_market_data(source, path)
    if source == "duckdb":
        frames = read_research_frames(path)
    elif source == "parquet_dir":
        frames = {name: pd.read_parquet(path / f"{name}.parquet") for name in RESEARCH_FILES
                  if (path / f"{name}.parquet").exists()}
    else:
        raise ValueError(f"unknown research data source {source!r}")
    data = ResearchData.from_frames(market, frames, {"source": str(path), **market.metadata})
    from ..fundamentals.pit import load_fundamentals  # stage 3 (P3); absent datasets -> None

    data.fundamentals = load_fundamentals(source, path, market)
    return data
