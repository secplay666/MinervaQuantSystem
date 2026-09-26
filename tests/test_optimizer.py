"""Stage-3 P5: risk model and mean-variance optimizer."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("osqp")

from research_fakes import synthetic_frames  # noqa: E402
from test_multifactor import _config, _run  # noqa: E402
from quant_system.portfolio.optimizer import OptimizerSpec, optimize  # noqa: E402


def _problem(seed: int = 0, n: int = 60, k: int = 5):
    rng = np.random.default_rng(seed)
    X = np.zeros((n, k))
    X[np.arange(n), rng.integers(0, 3, n)] = 1.0  # three industries
    X[:, 3:] = rng.normal(size=(n, 2))           # two styles
    F = np.diag([0.002, 0.002, 0.002, 0.001, 0.0015])
    D = rng.uniform(0.005, 0.02, n)
    alpha = rng.normal(0, 0.01, n)
    w_bench = np.full(n, 1 / n)
    return X, F, D, alpha, X.T @ w_bench


def test_optimizer_solution_satisfies_every_constraint() -> None:
    X, F, D, alpha, bench_exposure = _problem()
    n = len(alpha)
    w0 = np.zeros(n)
    w0[:20] = 0.05
    spec = OptimizerSpec(max_weight=0.06, industry_active=0.05, style_bounds={}, max_turnover=0.3)
    ub, lb = np.full(n, 0.06), np.zeros(n)
    ub[0] = 0.0                       # forced exit
    lb[1], ub[1] = w0[1], w0[1]       # frozen (no bar)
    weights, status = optimize(alpha, X, F, D, bench_exposure, w0, lb, ub, np.full(n, 1e-4), np.full(n, 2e-4),
                               [0, 1, 2], {3: 0.3}, spec, "full", 1.0, float(w0[0]))
    assert status == "solved"
    assert weights.sum() == pytest.approx(1.0, abs=2e-3) and (weights <= ub + 1e-6).all()
    assert weights[0] == pytest.approx(0, abs=1e-6) and weights[1] == pytest.approx(w0[1], abs=1e-6)
    active = X.T @ weights - bench_exposure
    assert (np.abs(active[:3]) <= 0.05 + 1e-6).all() and abs(active[3]) <= 0.3 + 1e-6
    # OSQP eps 1e-6 accumulates over ~120 trade variables: allow 0.05% of the portfolio.
    assert np.abs(weights - w0).sum() <= 2 * 0.3 + w0[0] + 5e-4


def test_infeasible_problems_return_no_weights() -> None:
    X, F, D, alpha, bench_exposure = _problem(1)
    n = len(alpha)
    spec = OptimizerSpec(industry_active=0.0, style_bounds={})
    ub = np.full(n, 0.2)
    ub[X[:, 0] == 1] = 0.0  # industry 0 is un-investable but must match the benchmark exactly
    for level in ("full", "no_turnover", "wide_bounds", "no_style_bounds"):
        weights, status = optimize(alpha, X, F, D, bench_exposure, np.zeros(n), np.zeros(n), ub, np.zeros(n),
                                   np.zeros(n), [0, 1, 2], {}, spec, level, 1.0, 0.0)
        assert weights is None and "infeasible" in status


def _optimizer_config(**optimizer):
    return _config(constructor="optimizer", optimizer={"candidates": 30, "max_weight": 0.2, "industry_active": 0.1,
                                                       "style_bounds": {}, "max_turnover": 0.5,
                                                       "risk_model": {"styles": ["vol_60", "turn_20"]}, **optimizer})


def test_multifactor_with_optimizer_runs_deterministically() -> None:
    market, research = synthetic_frames(5, sessions=330, symbols=40)
    first, summary, _ = _run(market, research, _optimizer_config())
    second, _, _ = _run(market, research, _optimizer_config())
    assert first.checks["replay_matches"] and summary["counters"]["fills"] > 0
    np.testing.assert_allclose(first.nav["nav_fen"], second.nav["nav_fen"])
    concentration = first.extras["concentration"]
    assert "diag_ex_ante_te" in concentration and (concentration["diag_ex_ante_te"] >= 0).all()


def test_optimizer_falls_back_to_rules_when_nothing_is_feasible() -> None:
    market, research = synthetic_frames(6, sessions=330, symbols=40)
    feasible, _, _ = _run(market, research, _optimizer_config())
    assert (feasible.extras["concentration"]["diag_optimizer_level_index"] < 4).all()
    # Zero industry deviation with only 5 candidates cannot match every benchmark industry.
    result, _, _ = _run(market, research, _optimizer_config(industry_active=0.0, candidates=5))
    concentration = result.extras["concentration"]
    assert result.checks["replay_matches"]
    # Candidates plus inherited holdings may happen to cover every industry; otherwise the rules take over.
    assert (concentration["diag_optimizer_level_index"] == 4).any() and (concentration["names"] > 0).all()
