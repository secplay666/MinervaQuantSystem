"""Shared inputs for factor computation (memoized per research dataset).

All panels are [T, N] float64 aligned with the research data; values on a
row only use that session and earlier ones.  Prices are hfq-adjusted where
returns are involved (ADR-004); levels (market cap, amplitude) use
unadjusted prices.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ..research.data import ResearchData

EXCLUDED_MARKET_BOARDS = ("BSE",)


class FactorContext:
    def __init__(self, research: ResearchData) -> None:
        self.research = research
        self.market = research.market
        self._memo: dict[str, Any] = {}

    def memo(self, name: str, build: Callable[[], Any]) -> Any:
        if name not in self._memo:
            self._memo[name] = build()
        return self._memo[name]

    @property
    def shape(self) -> tuple[int, int]:
        return self.market.shape

    # -- prices and returns ---------------------------------------------------

    def _yuan(self, name: str) -> np.ndarray:
        market = self.market
        return self.memo(f"{name}_yuan", lambda: np.where(
            market.has_bar, getattr(market, name).astype(np.float64) / 100.0, np.nan))

    def open(self) -> np.ndarray:
        return self._yuan("open")

    def high(self) -> np.ndarray:
        return self._yuan("high")

    def low(self) -> np.ndarray:
        return self._yuan("low")

    def close(self) -> np.ndarray:
        return self._yuan("close")

    def adj_close(self) -> np.ndarray:
        """close x hfq on bar days, NaN otherwise."""
        return self.memo("adj_close", lambda: self.close() * self.market.hfq)

    def adj_last(self) -> np.ndarray:
        """Adjusted close of the last bar on or before each session (as in
        ``PanelView.adjusted_price``); NaN before the first bar."""
        def build() -> np.ndarray:
            market = self.market
            rows = np.maximum(market.last_bar, 0)
            close = np.take_along_axis(market.close, rows, axis=0).astype(np.float64)
            hfq = np.take_along_axis(market.hfq, rows, axis=0)
            return np.where(market.last_bar >= 0, close * hfq, np.nan)

        return self.memo("adj_last", build)

    def returns(self) -> np.ndarray:
        """Bar-to-bar adjusted return on bar days (a return after a
        suspension spans the gap); NaN without a previous bar."""
        def build() -> np.ndarray:
            previous = np.full(self.shape, np.nan)
            previous[1:] = self.adj_last()[:-1]
            with np.errstate(invalid="ignore", divide="ignore"):
                return self.adj_close() / previous - 1.0

        return self.memo("returns", build)

    def reference_close(self) -> np.ndarray:
        """Previous close in today's price terms (ex-rights adjusted)."""
        def build() -> np.ndarray:
            previous = np.full(self.shape, np.nan)
            previous[1:] = self.adj_last()[:-1]
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(self.market.has_bar, previous / self.market.hfq, np.nan)

        return self.memo("reference_close", build)

    def market_returns(self) -> np.ndarray:
        """Equal-weighted mean return of all traded A shares (BSE excluded),
        a universe-independent market proxy [T]."""
        def build() -> np.ndarray:
            included = ~np.isin(self.market.board, EXCLUDED_MARKET_BOARDS)
            returns = self.returns()[:, included]
            with np.errstate(invalid="ignore"):
                counts = np.isfinite(returns).sum(axis=1)
                totals = np.nansum(returns, axis=1)
            return np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)

        return self.memo("market_returns", build)

    # -- activity -------------------------------------------------------------

    def turnover_cny(self) -> np.ndarray:
        return self.memo("turnover_cny", lambda: np.where(self.market.has_bar, self.market.turnover, np.nan))

    def volume(self) -> np.ndarray:
        return self.memo("volume", lambda: np.where(self.market.has_bar, self.market.volume, np.nan))

    def turnover_rate(self) -> np.ndarray:
        """Daily volume / tradable A shares (fraction), NaN when unknown."""
        def build() -> np.ndarray:
            shares = self.research.float_shares
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(shares > 0, self.volume() / shares, np.nan)

        return self.memo("turnover_rate", build)

    # -- size -------------------------------------------------------------------

    def float_mcap(self) -> np.ndarray:
        return self.memo("float_mcap", lambda: self.research.market_cap("float"))

    def total_mcap(self) -> np.ndarray:
        return self.memo("total_mcap", lambda: self.research.market_cap("total"))

    def bar_coverage(self, window: int) -> np.ndarray:
        """Share of the last ``window`` sessions with a bar (NaN before a full window)."""
        def build() -> np.ndarray:
            counts = self.market.bar_count.astype(np.float64)
            out = np.full(self.shape, np.nan)
            out[window - 1:] = counts[window - 1:] - np.vstack([np.zeros((1, self.shape[1])), counts[:-window]])
            return out / window

        return self.memo(f"coverage_{window}", build)
