"""Point-in-time liquidity universe (ADR-009).

At each session row: eligible names (board, bar today, listed >= 250
sessions, bar coverage, known normal risk state, price floor, share data)
are ranked by 20-day average turnover and float market cap (equal weights
of their percentile ranks) and the top ``size`` form the universe.  Only
data up to the row is used; delisting dates are never read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..backtest.market_data import RISK_NORMAL
from ..features.context import FactorContext
from ..features.rolling import rolling_mean

DEFAULT_BOARDS = ("SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR")


@dataclass(frozen=True)
class UniverseSpec:
    size: int = 1800
    boards: tuple[str, ...] = DEFAULT_BOARDS
    min_listing_sessions: int = 250
    coverage_window: int = 60
    min_coverage: float = 0.8
    min_price: float = 2.0
    turnover_window: int = 20
    turnover_weight: float = 0.5
    exclude_risk_warning: bool = True

    @classmethod
    def from_payload(cls, payload: dict | None) -> "UniverseSpec":
        payload = dict(payload or {})
        if "boards" in payload:
            payload["boards"] = tuple(payload["boards"])
        return cls(**payload)

    def to_payload(self) -> dict:
        return {**asdict(self), "boards": list(self.boards)}


def pct_rank(values: np.ndarray) -> np.ndarray:
    """Average-rank percentile in (0, 1] of finite values; NaN elsewhere."""
    out = np.full(values.shape, np.nan)
    finite = np.isfinite(values)
    x = values[finite]
    if len(x) == 0:
        return out
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x))
    sorted_x = x[order]
    # average ranks for ties
    _, first, counts = np.unique(sorted_x, return_index=True, return_counts=True)
    average = first + (counts + 1) / 2.0
    ranks[order] = np.repeat(average, counts)
    out[finite] = ranks / len(x)
    return out


def universe_masks(ctx: FactorContext, rows: np.ndarray, spec: UniverseSpec) -> tuple[np.ndarray, pd.DataFrame]:
    """(mask [R, N], per-row diagnostics)."""
    market = ctx.market
    rows = np.asarray(rows, dtype=np.int64)
    board = np.isin(market.board, spec.boards)[None, :]
    has_bar = market.has_bar[rows]
    listed = (rows[:, None] - market.list_index[None, :]) >= spec.min_listing_sessions
    coverage = ctx.bar_coverage(spec.coverage_window)[rows] >= spec.min_coverage
    if spec.exclude_risk_warning:
        state = market.risk[rows]
        normal = np.where(market.risk_known[None, :], state == RISK_NORMAL, True)
    else:
        normal = np.ones(has_bar.shape, dtype=bool)
    price = np.nan_to_num(ctx.close()[rows], nan=0.0) >= spec.min_price
    turnover = ctx.memo(f"universe_turnover_{spec.turnover_window}", lambda: rolling_mean(
        ctx.turnover_cny(), spec.turnover_window, max(1, spec.turnover_window // 2)))[rows]
    cap = ctx.float_mcap()[rows]
    has_size = np.isfinite(turnover) & np.isfinite(cap) & (cap > 0)
    steps = [("board", board & np.ones_like(has_bar)), ("has_bar", has_bar), ("listed", listed),
             ("coverage", coverage), ("risk_normal", normal), ("price", price), ("share_data", has_size)]
    eligible = np.ones(has_bar.shape, dtype=bool)
    diagnostics = {"row": rows}
    for name, condition in steps:
        eligible &= condition
        diagnostics[f"after_{name}"] = eligible.sum(axis=1)
    mask = np.zeros(has_bar.shape, dtype=bool)
    for k in range(len(rows)):
        candidates = np.flatnonzero(eligible[k])
        if len(candidates) <= spec.size:
            mask[k, candidates] = True
            continue
        score = (spec.turnover_weight * pct_rank(turnover[k, candidates])
                 + (1 - spec.turnover_weight) * pct_rank(cap[k, candidates]))
        order = candidates[np.lexsort((candidates, -score))]  # ties: lower symbol first
        mask[k, order[:spec.size]] = True
    diagnostics["universe"] = mask.sum(axis=1)
    frame = pd.DataFrame(diagnostics)
    frame.insert(0, "session", [market.sessions[t] for t in rows])
    return mask, frame
