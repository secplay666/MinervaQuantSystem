"""Benchmarks aligned with the backtest's sessions.

The primary benchmark is an equal-weight *total-return* universe portfolio
built from the same point-in-time eligibility rule as the strategy,
rebalanced on the same schedule, with no costs.  Exchange indices are price
indices (dividends excluded) and are labelled as such.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ..backtest.market_data import MarketData
from ..backtest.view import PanelView
from ..domain.calendar import TradingCalendar


def adjusted_panel(data: MarketData) -> np.ndarray:
    """hfq-adjusted price per session, carried through suspensions."""
    columns = np.arange(data.shape[1])[None, :]
    last = data.last_bar
    safe = np.where(last >= 0, last, 0)
    price = data.close[safe, columns] * data.hfq[safe, columns]
    return np.where(last >= 0, price, np.nan)


def equal_weight_benchmark(
    data: MarketData,
    start: int,
    end: int,
    eligible: Callable[[PanelView], np.ndarray],
    is_rebalance: Callable[[int], bool],
) -> pd.Series:
    """Value path starting at 1.0 on session ``start``.

    On each rebalance close the portfolio resets to equal weights over the
    eligible names; between rebalances holdings drift with adjusted prices.
    Before the first rebalance it holds cash (value 1.0), like the strategy.
    """
    prices = adjusted_panel(data)
    values = np.ones(end - start + 1)
    held: np.ndarray | None = None
    base_prices: np.ndarray | None = None
    base_value = 1.0
    for offset, i in enumerate(range(start, end + 1)):
        if held is not None and len(held):
            relative = prices[i, held] / base_prices
            values[offset] = base_value * float(np.nanmean(relative))
        else:
            values[offset] = base_value
        if is_rebalance(i):
            mask = eligible(PanelView(data, i)) & np.isfinite(prices[i])
            held = np.flatnonzero(mask)
            base_prices = prices[i, held]
            base_value = values[offset]
    return pd.Series(values, index=list(data.sessions[start:end + 1]), name="equal_weight_tr")


def index_benchmark(data: MarketData, symbol: str, start: int, end: int) -> pd.Series:
    closes = pd.Series(data.indices[symbol][start:end + 1], index=list(data.sessions[start:end + 1]))
    closes = closes.ffill()
    return closes / closes.dropna().iloc[0]


def benchmarks_for(data: MarketData, start: int, end: int, eligible, schedule, indices: tuple[str, ...],
                   include_equal_weight: bool) -> dict[str, pd.Series]:
    calendar = TradingCalendar(data.sessions)
    series: dict[str, pd.Series] = {}
    if include_equal_weight:
        series["等权全收益基准"] = equal_weight_benchmark(
            data, start, end, eligible, lambda i: schedule.is_rebalance(calendar, i)
        )
    for symbol in indices:
        if symbol in data.indices:
            series[f"{symbol}（价格指数）"] = index_benchmark(data, symbol, start, end)
    return series
