"""Shareholder counts (股东户数) as a point-in-time store for the factor library (ADR-004, ADR-009).

A count is usable at the close of the first session after its notice date (the time of day is unknown, as for the
statements).  The factor reads the counts at quarter ends only: every company reports them, so the change spans
the same quarter for all.  At each session the store holds the latest quarter end known and its change from the
quarter end before it (missing when that count is unknown); it goes stale MAX_AGE_DAYS after the quarter end,
long enough to bridge the annual report and the first-quarter report, which may both come out in late April.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

MAX_AGE_DAYS = 240
COLUMNS = ["symbol", "end_date", "notice_date", "holders"]


@dataclass
class HolderStore:
    """The quarter-on-quarter change of the count as a step function of the session index, per symbol."""

    sessions: tuple[date, ...]
    n_symbols: int
    cols: np.ndarray      # int64 [E], sorted with eff
    eff: np.ndarray       # int64 [E]
    change: np.ndarray    # float64 [E]
    periods: np.ndarray   # datetime64[D] [E]: the quarter end the change ends at

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for array in (self.cols, self.eff, self.change, self.periods.astype("int64")):
            digest.update(np.ascontiguousarray(array).tobytes())
        digest.update(str(MAX_AGE_DAYS).encode())
        return digest.hexdigest()

    def asof(self, rows: np.ndarray) -> np.ndarray:
        """Change known at the close of each requested session row [R, N] (NaN: unknown or stale)."""
        rows = np.asarray(rows, dtype=np.int64)
        if not len(self.cols):
            return np.full((len(rows), self.n_symbols), np.nan)
        T = len(self.sessions)
        keys = self.cols * T + self.eff
        query = np.arange(self.n_symbols, dtype=np.int64)[None, :] * T + rows[:, None]
        position = np.searchsorted(keys, query, side="right") - 1
        safe = np.maximum(position, 0)
        valid = (position >= 0) & (self.cols[safe] == np.arange(self.n_symbols)[None, :])
        session_days = np.array(self.sessions, dtype="datetime64[D]")[rows][:, None]
        fresh = (session_days - self.periods[safe]).astype(np.int64) <= MAX_AGE_DAYS
        return np.where(valid & fresh, self.change[safe], np.nan)


def previous_quarter_end(day: date) -> date:
    return (pd.Timestamp(day) - pd.offsets.QuarterEnd(1)).date()


def derive_holder_store(frame: pd.DataFrame, sessions: tuple[date, ...], symbols: np.ndarray) -> HolderStore:
    columns = {str(symbol): j for j, symbol in enumerate(symbols)}
    frame = frame[frame["symbol"].astype(str).isin(columns) & (frame["holders"] > 0)]
    end = pd.to_datetime(frame["end_date"])
    frame = frame[end.dt.is_quarter_end.to_numpy()]
    session_days = np.array(sessions, dtype="datetime64[D]")
    notice = pd.to_datetime(frame["notice_date"]).to_numpy(dtype="datetime64[D]")
    eff = np.searchsorted(session_days, notice, side="right")
    events = pd.DataFrame({"col": frame["symbol"].astype(str).map(columns).to_numpy(), "eff": eff,
                           "end": pd.to_datetime(frame["end_date"]).dt.date.to_numpy(),
                           "holders": frame["holders"].to_numpy(dtype=np.float64)})
    events = events[events["eff"] < len(sessions)].sort_values(["col", "eff", "end"], kind="stable")
    cols, effs, changes, periods = [], [], [], []
    for col, group in events.groupby("col", sort=True):
        known: dict[date, float] = {}
        for eff_row, end_day, holders in zip(group["eff"], group["end"], group["holders"]):
            known[end_day] = holders
            latest = max(known)
            before = known.get(previous_quarter_end(latest))
            cols.append(col)
            effs.append(eff_row)
            changes.append(known[latest] / before - 1.0 if before else np.nan)
            periods.append(latest)
    out = pd.DataFrame({"col": cols, "eff": effs, "change": changes, "period": periods})
    out = out.drop_duplicates(["col", "eff"], keep="last")  # the last count of a session wins
    return HolderStore(sessions=tuple(sessions), n_symbols=len(symbols), cols=out["col"].to_numpy(dtype=np.int64),
                       eff=out["eff"].to_numpy(dtype=np.int64), change=out["change"].to_numpy(dtype=np.float64),
                       periods=np.array(out["period"].tolist(), dtype="datetime64[D]"))


def read_holder_counts(source: str, path: Path) -> pd.DataFrame | None:
    if source == "duckdb":
        with duckdb.connect(str(path), read_only=True) as con:
            if "shareholder_counts" not in {row[0] for row in con.execute("SHOW TABLES").fetchall()}:
                return None
            return con.execute(f"SELECT {', '.join(COLUMNS)} FROM shareholder_counts "
                               "ORDER BY symbol, end_date").fetchdf()
    if source == "parquet_dir":
        file = Path(path) / "shareholder_counts.parquet"
        return pd.read_parquet(file, columns=COLUMNS) if file.exists() else None
    return None


def load_holder_store(source: str, path: Path, market: Any) -> HolderStore | None:
    frame = read_holder_counts(source, path)
    if frame is None or frame.empty:
        return None
    return derive_holder_store(frame, market.sessions, market.symbols)
