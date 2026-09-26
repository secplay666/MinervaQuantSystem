"""Rule-based portfolio construction (ADR-007).

Scores -> target weights under explainable rules:

1. rank candidates (score, ties by symbol);
2. keep current holdings ranked within ``buffer * N`` (turnover control);
3. per-industry name counts within the benchmark industry weight +/- delta
   (delta is widened, and recorded, when the minima cannot fit in N);
4. names whose minimum buy lot costs more than ``lot_tolerance`` x the
   target value are replaced by the next name (recorded, so the tilt
   towards low-priced stocks it causes can be reported);
5. equal or score-tilted weights, capped per name at
   min(max_weight, adv_participation x ADV x liquidity_days / NAV) by
   repeated clipping and redistribution.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass(frozen=True)
class RuleConstructionSpec:
    n_holdings: int = 100
    buffer: float = 1.5
    industry_deviation: float = 0.05
    max_weight: float = 0.02
    weighting: str = "equal"          # equal | score
    score_tilt: float = 0.5
    adv_participation: float = 0.10
    liquidity_days: float = 1.0
    lot_tolerance: float = 1.2
    cash_buffer: float = 0.0  # the order sizer keeps its own cash buffer

    def __post_init__(self) -> None:
        if self.weighting not in {"equal", "score"}:
            raise ValueError("construction.weighting must be equal or score")
        if self.n_holdings < 1 or self.buffer < 1:
            raise ValueError("n_holdings >= 1 and buffer >= 1 required")

    @classmethod
    def from_payload(cls, payload: dict | None) -> "RuleConstructionSpec":
        return cls(**dict(payload or {}))

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass
class ConstructionResult:
    weights: np.ndarray                      # [N], sums to <= 1
    selected: np.ndarray                     # column indices, in rank order
    diagnostics: dict = field(default_factory=dict)


def _ranks(scores: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Columns of ``candidates`` ordered best first; ties by column (symbol)."""
    return candidates[np.lexsort((candidates, -scores[candidates]))]


def industry_bounds(bench: dict[int, float], n: int, delta: float) -> tuple[dict[int, int], dict[int, int], float]:
    """Per-industry (min, max) name counts, widening delta until the minima fit."""
    while True:
        low = {k: max(0, math.ceil((w - delta) * n - 1e-9)) for k, w in bench.items()}
        high = {k: max(low[k], math.floor((w + delta) * n + 1e-9)) for k, w in bench.items()}
        if sum(low.values()) <= n or delta >= 1:
            return low, high, delta
        delta = max(delta * 1.5, delta + 0.01)


def cap_weights(weights: np.ndarray, caps: np.ndarray, total: float) -> np.ndarray:
    """Scale to ``total`` then clip at ``caps`` and redistribute the excess
    over uncapped names until nothing exceeds its cap (cash if impossible)."""
    w = weights / weights.sum() * total
    for _ in range(100):
        over = w > caps + 1e-15
        if not over.any():
            break
        excess = (w[over] - caps[over]).sum()
        w[over] = caps[over]
        free = (~over) & (w < caps - 1e-15)
        if not free.any() or excess <= 0:
            break
        w[free] += excess * w[free] / w[free].sum()
    return np.minimum(w, caps)


def construct_rules(scores: np.ndarray, eligible: np.ndarray, industry: np.ndarray, current: np.ndarray,
                    prices_yuan: np.ndarray, min_lot: np.ndarray, adv_cny: np.ndarray, nav_cny: float,
                    spec: RuleConstructionSpec) -> ConstructionResult:
    """All arrays are [N] over the symbol axis; ``current`` holds current weights."""
    n = spec.n_holdings
    candidates = np.flatnonzero(eligible & np.isfinite(scores))
    order = _ranks(scores, candidates)
    rank = np.full(len(scores), np.iinfo(np.int64).max)
    rank[order] = np.arange(len(order))
    universe_industries = industry[eligible]
    bench = {int(k): float(c) / max(len(universe_industries), 1)
             for k, c in zip(*np.unique(universe_industries, return_counts=True))}
    low, high, delta = industry_bounds(bench, n, spec.industry_deviation)
    target_value = nav_cny * (1 - spec.cash_buffer) / n
    lot_cost = prices_yuan * min_lot
    too_expensive = np.isfinite(lot_cost) & (lot_cost > spec.lot_tolerance * target_value)

    count: dict[int, int] = {k: 0 for k in bench}
    chosen: list[int] = []
    skipped_lot: list[int] = []

    def add(j: int) -> bool:
        k = int(industry[j])
        if j in chosen or count.get(k, 0) >= high.get(k, n):
            return False
        if too_expensive[j]:
            if j not in skipped_lot:
                skipped_lot.append(int(j))
            return False
        chosen.append(int(j))
        count[k] = count.get(k, 0) + 1
        return True

    held = np.flatnonzero((current > 0) & eligible & (rank < spec.buffer * n))
    kept = [int(j) for j in held[np.argsort(rank[held], kind="stable")]]
    for j in kept:
        add(j)
    for k, minimum in sorted(low.items()):
        members = [j for j in order if int(industry[j]) == k]
        for j in members:
            if count.get(k, 0) >= minimum or len(chosen) >= n:
                break
            add(j)
    for j in order:
        if len(chosen) >= n:
            break
        add(j)
    selected = np.array(sorted(chosen, key=lambda j: rank[j]), dtype=np.int64)
    weights = np.zeros(len(scores))
    diagnostics = {"candidates": int(len(candidates)), "kept": int(sum(j in chosen for j in kept)),
                   "industry_delta": delta, "lot_skipped": len(skipped_lot), "selected": int(len(selected))}
    if len(selected) == 0:
        return ConstructionResult(weights, selected, diagnostics)
    if spec.weighting == "score":
        z = scores[selected]
        z = (z - z.mean()) / z.std() if z.std() > 0 else np.zeros_like(z)
        raw = np.maximum(1 + spec.score_tilt * z, 0.2)
    else:
        raw = np.ones(len(selected))
    with np.errstate(invalid="ignore", divide="ignore"):
        liquidity_cap = np.where(adv_cny[selected] > 0,
                                 spec.adv_participation * adv_cny[selected] * spec.liquidity_days / nav_cny, 0.0)
    caps = np.minimum(spec.max_weight, np.nan_to_num(liquidity_cap))
    weights[selected] = cap_weights(raw, caps, 1.0 - spec.cash_buffer)
    diagnostics["capped"] = int((weights[selected] >= caps - 1e-12).sum())
    diagnostics["invested"] = float(weights.sum())
    diagnostics["lot_skipped_symbols"] = skipped_lot[:50]
    return ConstructionResult(weights, selected, diagnostics)
