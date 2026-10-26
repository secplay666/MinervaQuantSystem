"""大涨后股东户数大增 (docs/design/position-manager.md §11.8): a quarter-end shareholder count up at least ``rise``
on the quarter before, announced after the stock rose at least ``run`` over RUN_SESSIONS sessions — the shares
spreading to many new hands after a markup, the visible side of distribution.

research/stock_flow/holder_alert_grid.py (2016–2026, in-sample, so the size is optimistic): with run 50% and
rise 20%, 2,706 disclosures; over the next 60 sessions the excess return averaged -3.9%, 31% beat the market,
45% fell 20% or more at some point, and the average was negative in 10 of 11 years.  A reminder, not an order.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..fundamentals.holders import previous_quarter_end

RUN_SESSIONS = 120
DEFAULT_RUN = 0.5
DEFAULT_RISE = 0.2


@dataclass(frozen=True)
class Surge:
    day: date          # the first session on or after the notice date
    end_date: date     # the quarter end of the count
    notice_date: date
    change: float      # on the quarter end before
    run: float         # the stock's return over RUN_SESSIONS sessions up to the notice date


def quarter_changes(counts: pd.DataFrame) -> list[tuple[date, date, float]]:
    """(quarter end, notice date, change on the quarter end before) of every count whose previous quarter is known."""
    rows = counts[counts["holders"] > 0]
    rows = rows[pd.to_datetime(rows["end_date"]).dt.is_quarter_end.to_numpy()]
    ends = pd.to_datetime(rows["end_date"]).dt.date.tolist()
    notices = pd.to_datetime(rows["notice_date"]).dt.date.tolist()
    known = dict(zip(ends, rows["holders"].astype(float)))
    out = []
    for end, notice in zip(ends, notices):
        before = known.get(previous_quarter_end(end))
        if before:
            out.append((end, notice, known[end] / before - 1.0))
    return out


def surges(dates: list[date], close: np.ndarray, counts: pd.DataFrame, run: float = DEFAULT_RUN,
           rise: float = DEFAULT_RISE) -> list[Surge]:
    """The disclosures that meet the rule, on the stock's own bars (closes in any one adjustment)."""
    out = []
    for end, notice, change in quarter_changes(counts):
        if change < rise:
            continue
        last = bisect_right(dates, notice) - 1  # the last bar on or before the notice date
        first = bisect_left(dates, notice)
        if last < RUN_SESSIONS or first >= len(dates):
            continue
        gain = float(close[last] / close[last - RUN_SESSIONS] - 1.0)
        if gain >= run:
            out.append(Surge(dates[first], end, notice, change, gain))
    return out
