"""Multi-factor stock selection (stage 3; ADR-007, ADR-009).

At setup the strategy computes, for every rebalance row up to the end of
the backtest, the liquidity universe and the processed factor panel (with
the factor cache).  At each rebalance close it reads only that row through
``FactorPanel.at(view)``:

* composite = mean over families of the mean member z-score (families
  without data count as 0; at least ``min_families`` must have data), or
* IC-weighted: factor weights proportional to the positive mean rank IC of
  past periods whose returns were fully known at the signal close
  (period k is known once the open after rebalance k+1 has traded);

then a portfolio constructor turns scores into weights.  The current
holdings (engine portfolio state) drive the turnover buffer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..backtest.market_data import MarketData
from ..domain.entities import TargetPortfolio
from ..domain.rules import MarketRules
from ..features.context import FactorContext
from ..features.processing import ProcessingSpec
from ..features.registry import FAMILIES, all_factors, get
from ..features.store import FactorCache, FactorPanel, build_factor_panel
from ..portfolio.construction import RuleConstructionSpec, construct_rules
from ..universe.liquidity import UniverseSpec, pct_rank, universe_masks
from .base import PortfolioState, build_schedule
from .registry import register_strategy

LOGGER = logging.getLogger(__name__)
WARMUP_ROWS = 250


@dataclass(frozen=True)
class MultiFactorParams:
    factors: tuple[str, ...]
    weighting: str = "family_equal"      # family_equal | ic
    min_families: int = 4
    ic_lookback: int = 24
    ic_min_periods: int = 12
    constructor: str = "rules"           # rules | optimizer
    universe: UniverseSpec = field(default_factory=UniverseSpec)
    processing: ProcessingSpec = field(default_factory=ProcessingSpec)
    construction: RuleConstructionSpec = field(default_factory=RuleConstructionSpec)
    optimizer: dict[str, Any] = field(default_factory=dict)
    use_cache: bool = True

    def __post_init__(self) -> None:
        if self.weighting not in {"family_equal", "ic"}:
            raise ValueError("weighting must be family_equal or ic")
        if self.constructor not in {"rules", "optimizer"}:
            raise ValueError("constructor must be rules or optimizer")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MultiFactorParams":
        payload = dict(payload)
        factors = payload.pop("factors", "all")
        ids = tuple(s.id for s in all_factors()) if factors == "all" else tuple(factors)
        for factor_id in ids:
            get(factor_id)
        return cls(
            factors=ids, weighting=payload.pop("weighting", "family_equal"),
            min_families=int(payload.pop("min_families", 4)), ic_lookback=int(payload.pop("ic_lookback", 24)),
            ic_min_periods=int(payload.pop("ic_min_periods", 12)), constructor=payload.pop("constructor", "rules"),
            universe=UniverseSpec.from_payload(payload.pop("universe", None)),
            processing=ProcessingSpec.from_payload(payload.pop("processing", None)),
            construction=RuleConstructionSpec.from_payload(payload.pop("construction", None)),
            optimizer=dict(payload.pop("optimizer", {})), use_cache=bool(payload.pop("use_cache", True)),
            **_no_extra(payload),
        )


def _no_extra(payload: dict[str, Any]) -> dict[str, Any]:
    if payload:
        raise ValueError(f"unknown multifactor params {sorted(payload)}")
    return {}


class MultiFactorStrategy:
    wants_portfolio_state = True
    strategy_id = "multifactor"

    def __init__(self, params: MultiFactorParams, version: str, research, rows: np.ndarray, panel: FactorPanel,
                 rules: MarketRules, ic_weights: dict[int, dict[str, float]] | None = None) -> None:
        self.params = params
        self.version = version
        self.research = research
        self.rows = rows
        self.panel = panel
        self.rules = rules
        self.ic_weights = ic_weights or {}
        market: MarketData = research.market
        self.min_lot = np.array([rules.lot_rule(str(board)).buy_min for board in market.board], dtype=np.float64)
        self.families = {factor_id: get(factor_id).family for factor_id in params.factors}
        self.records: list[dict[str, Any]] = []
        self._optimizer = None

    # -- universe (shared with the equal-weight benchmark) ---------------------

    def eligible(self, view) -> np.ndarray:
        return self.panel.universe_at(view).copy()

    # -- scores -----------------------------------------------------------------

    def family_scores(self, view) -> dict[str, np.ndarray]:
        values = self.panel.at(view)
        out = {}
        for family in FAMILIES:
            members = [values[f] for f, fam in self.families.items() if fam == family]
            if members:
                stack = np.vstack(members)
                with np.errstate(invalid="ignore"):
                    counts = np.isfinite(stack).sum(axis=0)
                    out[family] = np.where(counts > 0, np.nansum(stack, axis=0) / np.maximum(counts, 1), np.nan)
        return out

    def composite(self, view) -> np.ndarray:
        weights = self.ic_weights.get(int(view.t)) if self.params.weighting == "ic" else None
        if weights:
            values = self.panel.at(view)
            total = sum(weights.values())
            score = sum(w * np.nan_to_num(values[f]) for f, w in weights.items()) / total
            present = np.vstack([np.isfinite(values[f]) for f in weights]).any(axis=0)
            return np.where(present, score, np.nan)
        families = self.family_scores(view)
        stack = np.vstack(list(families.values()))
        present = np.isfinite(stack).sum(axis=0)
        score = np.nansum(stack, axis=0) / len(families)
        return np.where(present >= min(self.params.min_families, len(families)), score, np.nan)

    # -- target -----------------------------------------------------------------

    def on_close(self, view, state: PortfolioState) -> TargetPortfolio:
        market = self.research.market
        eligible = self.eligible(view)
        scores = self.composite(view)
        symbols = view.symbols
        current = np.zeros(len(symbols))
        for symbol, weight in state.weights.items():
            current[market.symbol_index(symbol)] = weight
        industry = self.research.industry[view.t]
        prices = view.close_fen().astype(np.float64) / 100.0
        adv = view.mean_turnover(20)
        nav = state.nav_fen / 100.0
        if self.params.constructor == "optimizer":
            from ..portfolio.optimizer import construct_optimized

            result = construct_optimized(self, view, scores, eligible, current, prices, adv, nav)
        else:
            result = construct_rules(scores, eligible, industry, current, prices, self.min_lot, adv, nav,
                                     self.params.construction)
        families = self.family_scores(view)
        ranks = {int(j): r + 1 for r, j in enumerate(result.selected)}
        codes = self.research.industry_codes
        explanations = {}
        weights = {}
        for j in result.selected:
            weight = float(result.weights[j])
            if weight <= 0:
                continue
            symbol = str(symbols[j])
            weights[symbol] = weight
            info = {"rank": ranks[int(j)], "score": round(float(scores[j]), 6),
                    "industry": codes[industry[j]] if industry[j] >= 0 else "unclassified"}
            for family, values in families.items():
                info[f"f_{family}"] = None if not np.isfinite(values[j]) else round(float(values[j]), 4)
            explanations[symbol] = info
        self.records.append({"t": int(view.t), "session": view.session, "weights": result.weights.copy(),
                             "universe": eligible, "diagnostics": result.diagnostics})
        return TargetPortfolio(view.session, weights, explanations)


    def report_tables(self) -> dict:
        from ..analytics.risk import exposure_tables

        return exposure_tables(self, self.rules)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def signal_rows(sessions, schedule: dict, end_index: int) -> np.ndarray:
    from ..domain.calendar import TradingCalendar

    calendar = TradingCalendar(sessions)
    rule = build_schedule(schedule)
    return np.array([i for i in range(WARMUP_ROWS, end_index + 1) if rule.is_rebalance(calendar, i)],
                    dtype=np.int64)


def trailing_ic_weights(research, panel: FactorPanel, rows: np.ndarray, schedule_rows: np.ndarray,
                        rules: MarketRules, params: MultiFactorParams) -> dict[int, dict[str, float]]:
    """{row: {factor: weight}} from rank ICs whose forward window closed
    on or before the row (strictly causal; see module docstring)."""
    from ..evaluation.factor_eval import EvaluationSpec, forward_returns

    ctx = FactorContext(research)
    sessions = research.sessions
    spec = EvaluationSpec("IS", sessions[0], sessions[-1], horizons=(1,), min_names=30)
    forward = forward_returns(ctx, rules, schedule_rows, rows, panel.universe, spec)
    known_at = forward.exit_sessions[1]  # session index of the exit open (-1 = never)
    ics: dict[str, np.ndarray] = {}
    for factor_id, values in panel.values.items():
        series = np.full(len(rows), np.nan)
        for k in range(len(rows)):
            fwd = forward.returns[1][k]
            valid = panel.universe[k] & forward.investable[k] & np.isfinite(values[k]) & np.isfinite(fwd)
            if valid.sum() >= 30:
                a, b = pct_rank(values[k][valid]), pct_rank(fwd[valid])
                a, b = a - a.mean(), b - b.mean()
                series[k] = float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
        ics[factor_id] = series
    weights: dict[int, dict[str, float]] = {}
    for j, t in enumerate(rows):
        known = [k for k in range(len(rows)) if 0 <= known_at[k] <= t][-params.ic_lookback:]
        if len(known) < params.ic_min_periods:
            continue
        means = {f: float(np.nanmean(ics[f][known])) for f in ics}
        positive = {f: m for f, m in means.items() if np.isfinite(m) and m > 0}
        if positive:
            weights[int(t)] = positive
    return weights


@register_strategy("multifactor")
def _build_multifactor(config, context) -> MultiFactorStrategy:
    params = MultiFactorParams.from_payload(config.strategy_params)
    research = context.research()
    if research.market is not context.data:
        raise ValueError("research data must share the backtest's market data")
    sessions = research.sessions
    end_index = max(i for i, day in enumerate(sessions) if day <= config.end)
    rows = signal_rows(sessions, config.schedule, end_index)
    ctx = FactorContext(research)
    universe, _ = universe_masks(ctx, rows, params.universe)
    cache = FactorCache(context.root / "data" / "features" if params.use_cache else None)
    panel = build_factor_panel(research, rows, list(params.factors), universe, params.processing, cache, ctx)
    rules = MarketRules.load(config.market_rules_path)
    ic_weights = None
    if params.weighting == "ic":
        from ..research.experiments import schedule_rows

        ic_weights = trailing_ic_weights(research, panel, rows, schedule_rows(sessions, config.schedule), rules,
                                         params)
    return MultiFactorStrategy(params, config.strategy_version, research, rows, panel, rules, ic_weights)
