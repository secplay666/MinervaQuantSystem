"""Stage-3 P4: rule-based construction and the multi-factor strategy."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bt_fakes import config as bt_config
from research_fakes import build, synthetic_frames, truncate
from quant_system.backtest.market_data import MarketData
from quant_system.backtest.runner import run_backtest
from quant_system.domain.rules import MarketRules
from quant_system.portfolio.construction import RuleConstructionSpec, cap_weights, construct_rules, industry_bounds
from quant_system.research.data import ResearchData
from quant_system.strategy.registry import StrategyContext

ROOT = Path(__file__).resolve().parents[1]
RULES = MarketRules.load(ROOT / "configs" / "market_rules" / "cn_a_share.json")


def test_cap_weights_redistributes_excess_and_leaves_cash_when_caps_bind() -> None:
    w = cap_weights(np.array([5.0, 1.0, 1.0, 1.0]), np.full(4, 0.4), 1.0)
    assert w.max() <= 0.4 + 1e-12 and w.sum() == pytest.approx(1.0)
    tight = cap_weights(np.ones(4), np.full(4, 0.2), 1.0)
    assert tight.sum() == pytest.approx(0.8)


def test_industry_bounds_widen_delta_until_minima_fit() -> None:
    low, high, delta = industry_bounds({0: 0.5, 1: 0.3, 2: 0.2}, 10, 0.0)
    assert sum(low.values()) <= 10 and delta >= 0.0
    low, high, delta = industry_bounds({k: 1 / 30 for k in range(30)}, 10, 0.0)
    assert sum(low.values()) <= 10 and delta > 0.0


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(0, 10_000), n=st.integers(3, 25), held=st.integers(0, 20))
def test_rule_construction_respects_every_constraint(seed: int, n: int, held: int) -> None:
    rng = np.random.default_rng(seed)
    size = 120
    scores = rng.normal(size=size)
    scores[rng.random(size) < 0.1] = np.nan
    eligible = rng.random(size) < 0.9
    industry = rng.integers(0, 6, size).astype(np.int16)
    current = np.zeros(size)
    current[rng.choice(size, held, replace=False)] = 1.0 / max(held, 1)
    prices = rng.uniform(2, 300, size)
    min_lot = np.where(rng.random(size) < 0.2, 200.0, 100.0)
    adv = rng.uniform(1e5, 1e9, size)
    spec = RuleConstructionSpec(n_holdings=n, max_weight=0.3, industry_deviation=0.1)
    nav = 1e7
    result = construct_rules(scores, eligible, industry, current, prices, min_lot, adv, nav, spec)
    w = result.weights
    assert (w >= 0).all() and w.sum() <= 1 + 1e-9
    assert (w[~eligible] == 0).all() and (w[~np.isfinite(scores)] == 0).all()
    assert (w > 0).sum() <= n
    caps = np.minimum(spec.max_weight, spec.adv_participation * adv * spec.liquidity_days / nav)
    assert (w <= caps + 1e-12).all()
    target = nav / n
    assert not ((w > 0) & (prices * min_lot > spec.lot_tolerance * target)).any()
    # Holdings ranked within the buffer are kept whenever they are allowed at all.
    order = np.flatnonzero(eligible & np.isfinite(scores))
    order = order[np.lexsort((order, -scores[order]))]
    rank = {int(j): r for r, j in enumerate(order)}
    for j in np.flatnonzero(current > 0):
        allowed = eligible[j] and np.isfinite(scores[j]) and prices[j] * min_lot[j] <= spec.lot_tolerance * target
        if allowed and rank[int(j)] < n:  # inside N: kept unless its industry is full
            counts = np.bincount(industry[result.selected], minlength=6)
            assert j in result.selected or counts[industry[j]] > 0


def _frames(seed: int = 5):
    market, research = synthetic_frames(seed, sessions=330, symbols=40)
    return market, research


def _config(end: str | None = None, **params):
    base = {"factors": ["vol_60", "rev_1m", "turn_20", "amihud_20", "amplitude_20"], "min_families": 2,
            "universe": {"size": 25, "min_listing_sessions": 60, "coverage_window": 20},
            "processing": {"min_names": 5},
            "construction": {"n_holdings": 6, "max_weight": 0.3, "industry_deviation": 0.2},
            "use_cache": False}
    base.update(params)
    return bt_config(strategy={"id": "multifactor", "version": "1", "params": base},
                     run={"start": "2020-12-01", "end": end or "2021-04-30", "initial_capital_cny": "10000000"},
                     benchmarks={"indices": [], "equal_weight_universe": True})


def _run(market: dict, research: dict, cfg):
    data = MarketData.from_frames(market)
    rd = ResearchData.from_frames(data, research)
    context = StrategyContext(ROOT, data, lambda: rd)
    return run_backtest(cfg, data, context)


def test_multifactor_backtest_runs_and_reports_exposures() -> None:
    market, research = _frames()
    result, summary, values = _run(market, research, _config())
    assert result.checks["replay_matches"] and summary["counters"]["fills"] > 0
    assert summary["counters"]["rebalance_signals"] >= 3
    signals = result.signals
    assert {"rank", "score", "industry", "f_volatility", "f_liquidity"} <= set(signals.columns)
    assert signals.groupby("as_of")["weight"].sum().max() <= 1 + 1e-9
    assert {"style_exposures", "industry_weights", "concentration", "brinson"} <= set(result.extras)
    assert "等权全收益基准" in values


def test_multifactor_backtest_ignores_data_after_the_cut() -> None:
    market, research = _frames(9)
    full, _, _ = _run(market, research, _config())
    sessions = sorted(market["calendar"]["trade_date"])
    cut = sessions[300]
    part, _, _ = _run(*truncate(market, research, cut), _config())
    for name in ("nav", "fills", "positions"):
        a = getattr(full, name)
        b = getattr(part, name)
        a = a[a["session"] <= cut].reset_index(drop=True)
        b = b[b["session"] <= cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=name)


def test_ic_weights_only_use_periods_known_at_the_signal() -> None:
    from quant_system.features.context import FactorContext
    from quant_system.features.processing import ProcessingSpec
    from quant_system.features.store import build_factor_panel
    from quant_system.research.experiments import schedule_rows
    from quant_system.strategy.multifactor import MultiFactorParams, trailing_ic_weights
    from quant_system.universe.liquidity import UniverseSpec, universe_masks

    market, research = synthetic_frames(17, sessions=420, symbols=60)
    sessions = sorted(market["calendar"]["trade_date"])
    params = MultiFactorParams.from_payload({"factors": ["vol_60", "rev_1m", "turn_20"], "weighting": "ic",
                                             "ic_min_periods": 2, "ic_lookback": 12,
                                             "universe": {"size": 40, "min_listing_sessions": 60,
                                                          "coverage_window": 20},
                                             "processing": {"min_names": 5}})

    def weights(frames):
        data = build(*frames)
        rows_all = schedule_rows(data.sessions, {"type": "month_end"})
        rows = rows_all[rows_all >= 60]
        ctx = FactorContext(data)
        universe, _ = universe_masks(ctx, rows, params.universe)
        panel = build_factor_panel(data, rows, list(params.factors), universe, ProcessingSpec(min_names=5), None, ctx)
        return trailing_ic_weights(data, panel, rows, rows_all, RULES, params)

    full = weights((market, research))
    cut = sessions[330]
    perturbed = dict(market)
    bars = market["bars"].copy()
    later = bars["trade_date"] > cut
    for column in ("open", "high", "low", "close"):
        bars.loc[later, column] = (bars.loc[later, column] * 1.37).round(2)
    perturbed["bars"] = bars
    other = weights((perturbed, research))
    cut_row = sessions.index(cut)
    known = {t: w for t, w in full.items() if t <= cut_row}
    assert known and {t: w for t, w in other.items() if t <= cut_row} == known
