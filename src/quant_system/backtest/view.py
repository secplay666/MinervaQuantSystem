"""Point-in-time view of market data for strategies (ADR-002).

A view is fixed at session ``t``: every accessor reads rows <= t only and
refuses anything later.  It exposes no delisting dates, statuses, names or
risk-interval end dates.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from .market_data import RISK_NORMAL, MarketData

UNKNOWN_RISK = -1


class LookAheadError(IndexError):
    pass


class PanelView:
    def __init__(self, data: MarketData, t: int) -> None:
        if not 0 <= t < len(data.sessions):
            raise IndexError(t)
        self._data = data
        self.t = t

    @property
    def session(self) -> date:
        return self._data.sessions[self.t]

    @property
    def symbols(self) -> np.ndarray:
        return self._data.symbols

    @property
    def board(self) -> np.ndarray:
        return self._data.board

    def _row(self, lag: int) -> int:
        if lag < 0:
            raise LookAheadError(f"lag {lag} would read after {self.session}")
        row = self.t - lag
        if row < 0:
            raise IndexError(f"lag {lag} is before the first session")
        return row

    def has_bar(self, lag: int = 0) -> np.ndarray:
        return self._data.has_bar[self._row(lag)]

    def close_fen(self, lag: int = 0) -> np.ndarray:
        return self._data.close[self._row(lag)]

    def adjusted_price(self, lag: int = 0) -> np.ndarray:
        """hfq-adjusted price as of session t-lag, using the last bar on or
        before it (suspended names keep their last price)."""
        row = self._row(lag)
        last = self._data.last_bar[row]
        columns = np.arange(len(self.symbols))
        valid = last >= 0
        safe = np.where(valid, last, 0)
        price = self._data.close[safe, columns] * self._data.hfq[safe, columns]
        return np.where(valid, price, np.nan)

    def bars_in_window(self, oldest_lag: int, newest_lag: int) -> np.ndarray:
        """Number of sessions with a bar in [t-oldest_lag, t-newest_lag]."""
        newest = self._row(newest_lag)
        oldest = self._row(oldest_lag)
        before = self._data.bar_count[oldest - 1] if oldest > 0 else 0
        return self._data.bar_count[newest] - before

    def mean_turnover(self, window: int) -> np.ndarray:
        """Mean daily turnover (CNY) over the last ``window`` sessions with bars."""
        newest = self._row(0)
        oldest = self._row(window - 1)
        block = self._data.turnover[oldest:newest + 1]
        bars = self._data.has_bar[oldest:newest + 1]
        counts = bars.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(counts > 0, (block * bars).sum(axis=0) / counts, np.nan)

    def listing_age(self) -> np.ndarray:
        """Sessions since listing (large for listings before the data)."""
        return self.t - self._data.list_index

    def risk_state(self) -> np.ndarray:
        """Risk-warning code at t where the history is dated, else UNKNOWN_RISK."""
        state = self._data.risk[self._row(0)].astype(np.int8)
        return np.where(self._data.risk_known, state, UNKNOWN_RISK)

    def is_known_normal(self) -> np.ndarray:
        return self.risk_state() == RISK_NORMAL
