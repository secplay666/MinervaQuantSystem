"""Medium-term momentum: 120-session return skipping the latest 20 sessions.

Demo strategy for validating the engine (ADR-006), not an investment view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..domain.entities import TargetPortfolio

if TYPE_CHECKING:  # strategies depend on the view protocol, not the engine
    from ..backtest.view import PanelView


@dataclass(frozen=True)
class MomentumParams:
    lookback: int = 120
    skip: int = 20
    top_n: int = 50
    min_window_coverage: float = 0.9
    min_listing_sessions: int = 250
    min_avg_turnover_cny: float = 10_000_000.0
    turnover_window: int = 20
    boards: tuple[str, ...] = ("SSE_MAIN", "SZSE_MAIN", "CHINEXT", "STAR")
    exclude_known_risk_warning: bool = True


@dataclass
class MomentumStrategy:
    params: MomentumParams = field(default_factory=MomentumParams)
    strategy_id: str = "momentum"
    version: str = "1"

    def eligible(self, view: PanelView) -> np.ndarray:
        """Point-in-time universe filter shared with the benchmark."""
        p = self.params
        oldest = p.lookback + p.skip
        if view.t < oldest:
            return np.zeros(len(view.symbols), dtype=bool)
        window = oldest - p.skip + 1
        coverage = view.bars_in_window(oldest, p.skip) / window
        mask = (
            view.has_bar()
            & np.isin(view.board, p.boards)
            & (view.listing_age() >= p.min_listing_sessions)
            & (coverage >= p.min_window_coverage)
        )
        if p.min_avg_turnover_cny > 0:
            mask &= np.nan_to_num(view.mean_turnover(p.turnover_window)) >= p.min_avg_turnover_cny
        if p.exclude_known_risk_warning:
            mask &= view.risk_state() <= 0  # normal or unknown (SSE/BSE history)
        return mask

    def scores(self, view: PanelView) -> np.ndarray:
        p = self.params
        with np.errstate(invalid="ignore", divide="ignore"):
            return view.adjusted_price(p.skip) / view.adjusted_price(p.lookback + p.skip) - 1

    def on_close(self, view: PanelView) -> TargetPortfolio:
        if view.t < self.params.lookback + self.params.skip:
            return TargetPortfolio(view.session, {})
        mask = self.eligible(view)
        scores = self.scores(view)
        mask &= np.isfinite(scores)
        candidates = np.flatnonzero(mask)
        # Highest score first; ties broken by symbol (the symbol axis is sorted).
        order = candidates[np.lexsort((candidates, -scores[candidates]))]
        chosen = order[: self.params.top_n]
        if len(chosen) == 0:
            return TargetPortfolio(view.session, {})
        weight = 1.0 / len(chosen)
        symbols = view.symbols
        return TargetPortfolio(
            as_of=view.session,
            weights={str(symbols[j]): weight for j in chosen},
            explanations={
                str(symbols[j]): {"rank": rank + 1, "momentum": round(float(scores[j]), 6),
                                  "universe_size": int(len(candidates))}
                for rank, j in enumerate(chosen)
            },
        )
