"""Mean-variance portfolio construction with OSQP (stage 3 P5; ADR-007).

    maximize  alpha'w - lambda * active_risk(w) - buy_cost'b - sell_cost's
    variables x = [w (n), y (k), b (n), s (n)]
      y = X'w - X'w_bench           active factor exposures
      w - b + s = w0                trades from current weights, b, s >= 0
      sum(w) = invested             budget
      lb <= w <= ub                 caps; no-bar names frozen; forced exits ub = 0
      |y_industry| <= delta_i, |y_style| <= delta_s
      sum(b + s) <= 2 * max_turnover + forced exits
    active_risk = (w - w_b)'D(w - w_b) + y'Fy     (risk model, monthly)
    alpha = ic * sigma * z                        (Grinold; z = composite within candidates)

Only "solved" is accepted outright; "solved inaccurate" or an iteration-limit
stop is accepted when every constraint holds to 1e-5.  Weights below ``min_weight`` and names whose minimum lot exceeds
the target are then fixed at zero and the problem is solved once more.  If
no solution is accepted: drop the turnover budget -> double the active
bounds -> drop the style bounds -> rule-based construction; the level used
is recorded.  ``scipy``/``osqp`` are imported lazily (optional extra).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import numpy as np

from .construction import ConstructionResult, construct_rules

LOGGER = logging.getLogger(__name__)
FALLBACKS = ("full", "no_turnover", "wide_bounds", "no_style_bounds")


@dataclass(frozen=True)
class OptimizerSpec:
    risk_aversion: float = 20.0
    ic: float = 0.05
    candidates: int = 400
    max_weight: float = 0.02
    min_weight: float = 0.001
    industry_active: float = 0.05
    style_bounds: dict = field(default_factory=lambda: {"ln_float_mcap": 0.5})
    max_turnover: float = 0.5        # one-sided, per rebalance
    holding_months: float = 6.0      # trading costs are spread over the expected holding period
    slippage_bps: float = 5.0
    commission_rate: float = 0.00025
    adv_participation: float = 0.10
    liquidity_days: float = 1.0
    lot_tolerance: float = 1.2
    risk_model: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict | None) -> "OptimizerSpec":
        return cls(**dict(payload or {}))

    def to_payload(self) -> dict:
        return asdict(self)


# ADMM settings tried in order; the turnover row makes some problems slow to
# converge with OSQP's defaults (measured on real rebalances, stage 3 P5).
SOLVER_ATTEMPTS = ({"sigma": 1e-4}, {"rho": 1.0})


def _solve(P, q, A, l, u):
    import osqp

    result = None
    for settings in SOLVER_ATTEMPTS:
        problem = osqp.OSQP()
        problem.setup(P, q, A, l, u, verbose=False, eps_abs=1e-6, eps_rel=1e-6, polishing=True, max_iter=100_000,
                      **settings)
        result = problem.solve(raise_error=False)
        if str(result.info.status) in ("solved", "primal infeasible", "dual infeasible"):
            break
    return result


def optimize(alpha, X, F, D, w_bench_exposure, w0, lb, ub, cost_buy, cost_sell, industry_rows, style_rows,
             spec: OptimizerSpec, level: str, invested: float, forced: float):
    """Returns (weights or None, status)."""
    import scipy.sparse as sp

    n, k = X.shape
    lam = spec.risk_aversion
    P = sp.block_diag([sp.diags(2 * lam * D), sp.csc_matrix(2 * lam * F), sp.csc_matrix((2 * n, 2 * n))],
                      format="csc")
    P = sp.triu(P, format="csc")
    q = np.concatenate([-alpha, np.zeros(k), cost_buy, cost_sell])
    I_n, I_k = sp.identity(n, format="csc"), sp.identity(k, format="csc")
    Z = lambda r, c: sp.csc_matrix((r, c))  # noqa: E731
    blocks = [
        sp.hstack([sp.csc_matrix(X.T), -I_k, Z(k, n), Z(k, n)]),              # exposures
        sp.hstack([I_n, Z(n, k), -I_n, I_n]),                                   # trades
        sp.hstack([sp.csc_matrix(np.ones((1, n))), Z(1, k), Z(1, n), Z(1, n)]),  # budget
        sp.hstack([I_n, Z(n, k), Z(n, n), Z(n, n)]),                            # weight bounds
        sp.hstack([Z(k, n), I_k, Z(k, n), Z(k, n)]),                            # exposure bounds
        sp.hstack([Z(2 * n, n + k), sp.identity(2 * n, format="csc")]),          # b, s >= 0
    ]
    y_low, y_high = np.full(k, -1e3), np.full(k, 1e3)
    scale = 2.0 if level in ("wide_bounds", "no_style_bounds") else 1.0
    y_low[industry_rows], y_high[industry_rows] = -spec.industry_active * scale, spec.industry_active * scale
    if level != "no_style_bounds":
        for row, bound in style_rows.items():
            y_low[row], y_high[row] = -bound * scale, bound * scale
    floor = min(invested, float(ub.sum())) - 1e-3  # caps may not allow full investment
    lower = [w_bench_exposure, w0, [floor], lb, y_low, np.zeros(2 * n)]
    upper = [w_bench_exposure, w0, [invested], ub, y_high, np.ones(2 * n)]
    if level == "full":
        blocks.append(sp.hstack([Z(1, n + k), sp.csc_matrix(np.ones((1, 2 * n)))]))
        lower.append([0.0])
        upper.append([2 * spec.max_turnover + forced])
    A = sp.vstack(blocks, format="csc")
    l, u = np.concatenate(lower), np.concatenate(upper)
    if (l > u + 1e-12).any():
        return None, "invalid_bounds"
    result = _solve(P, q, A, l, u)
    status = str(result.info.status)
    accepted = ("solved", "solved inaccurate", "maximum iterations reached")
    if status not in accepted or result.x is None or not np.isfinite(result.x).all():
        return None, status
    x = result.x
    violation = max(float(np.max(l - A @ x, initial=0)), float(np.max(A @ x - u, initial=0)))
    if status != "solved" and violation > 1e-5:
        return None, f"{status} (violation {violation:.1e})"
    return np.clip(x[:n], 0.0, None), status


RISK_MODEL_FIRST_ROW = 60


def build_risk_model(strategy, spec):
    """Monthly exposures from an early month-end so the covariance at the
    first signal already has a year or more of daily factor returns."""
    from ..domain.calendar import TradingCalendar
    from ..features.context import FactorContext
    from ..universe.liquidity import universe_masks
    from .risk_model import RiskModel

    research = strategy.research
    calendar = TradingCalendar(research.sessions)
    last = int(strategy.rows[-1])
    rows = np.array([i for i in range(RISK_MODEL_FIRST_ROW, last + 1) if calendar.is_month_end(i)], dtype=np.int64)
    ctx = FactorContext(research)
    universe, _ = universe_masks(ctx, rows, strategy.params.universe)
    return RiskModel(research, rows, universe, spec, ctx)


def construct_optimized(strategy, view, scores, eligible, current, prices, adv, nav) -> ConstructionResult:
    from .risk_model import RiskModelSpec

    spec = OptimizerSpec.from_payload(strategy.params.optimizer)
    if strategy._optimizer is None:
        strategy._optimizer = build_risk_model(strategy, RiskModelSpec.from_payload(spec.risk_model))
    model = strategy._optimizer
    t = int(view.t)
    snapshot = model.at(t)
    market = strategy.research.market
    has_bar = market.has_bar[t]
    candidates = np.flatnonzero(eligible & np.isfinite(scores))
    top = candidates[np.lexsort((candidates, -scores[candidates]))][: spec.candidates]
    held = np.flatnonzero(current > 0)
    cols = np.array(sorted(set(top.tolist()) | set(held.tolist())), dtype=np.int64)
    position = {int(c): i for i, c in enumerate(snapshot.columns)}
    K = snapshot.exposures.shape[1]
    X = np.zeros((len(cols), K))
    D = np.full(len(cols), float(np.median(snapshot.specific_var)))
    for i, c in enumerate(cols):
        j = position.get(int(c))
        if j is not None:
            X[i], D[i] = snapshot.exposures[j], snapshot.specific_var[j]
    bench = np.zeros(market.shape[1])
    bench[snapshot.columns] = 1.0 / len(snapshot.columns)
    bench_exposure = snapshot.exposures.T @ bench[snapshot.columns]
    w0 = current[cols]
    z = scores[cols]
    finite = np.isfinite(z)
    z = np.where(finite, (z - np.nanmean(z)) / (np.nanstd(z) or 1.0), 0.0)
    sigma = np.sqrt(np.einsum("ik,kl,il->i", X, snapshot.factor_cov, X) + D)
    alpha = spec.ic * sigma * z
    with np.errstate(invalid="ignore", divide="ignore"):
        liquidity_cap = np.nan_to_num(spec.adv_participation * adv[cols] * spec.liquidity_days / nav)
    ub = np.minimum(spec.max_weight, liquidity_cap)
    lb = np.zeros(len(cols))
    exits = ~eligible[cols]
    ub[exits] = 0.0
    frozen = ~has_bar[cols]
    lb[frozen], ub[frozen] = w0[frozen], w0[frozen]
    forced = float(w0[exits & ~frozen].sum())
    rules = strategy.rules
    sell_duty = float(rules.stamp_duty_rate("sell", view.session))
    one_way = spec.commission_rate + spec.slippage_bps / 1e4
    cost_buy = np.full(len(cols), one_way / spec.holding_months)
    cost_sell = np.full(len(cols), (one_way + sell_duty) / spec.holding_months)
    names = snapshot.factor_names
    industry_rows = [i for i, name in enumerate(names) if name.startswith("ind_")]
    style_rows = {names.index(style): bound for style, bound in spec.style_bounds.items() if style in names}
    invested = 1.0  # frozen (suspended) weights stay inside the budget
    lot_cost = prices[cols] * strategy.min_lot[cols]
    weights, status, level = None, "not_run", "rules"
    for level in FALLBACKS:
        weights, status = optimize(alpha, X, snapshot.factor_cov, D, bench_exposure, w0, lb, ub, cost_buy, cost_sell,
                                   industry_rows, style_rows, spec, level, invested, forced)
        if weights is None:
            continue
        small = (weights < spec.min_weight) & ~frozen
        expensive = (weights > 0) & np.isfinite(lot_cost) & (lot_cost > spec.lot_tolerance * weights * nav) & ~frozen
        drop = small | expensive
        if drop.any():
            ub2 = ub.copy()
            ub2[drop] = 0.0
            lb2 = np.minimum(lb, ub2)
            again, status2 = optimize(alpha, X, snapshot.factor_cov, D, bench_exposure, w0, lb2, ub2, cost_buy,
                                      cost_sell, industry_rows, style_rows, spec, level, invested, forced)
            if again is not None:
                weights, status = again, status2
                weights[drop] = 0.0
        break
    if weights is None:
        result = construct_rules(scores, eligible, strategy.research.industry[t], current, prices, strategy.min_lot,
                                 adv, nav, strategy.params.construction)
        result.diagnostics.update({"optimizer_level": "rules", "optimizer_level_index": len(FALLBACKS),
                                   "optimizer_status": status})
        return result
    weights = np.clip(weights, 0.0, None)
    weights[weights < 1e-9] = 0.0
    total = weights.sum()
    if total > 1.0:  # solver tolerance: never target more than the portfolio
        weights *= (1.0 - 1e-12) / total
    full = np.zeros(market.shape[1])
    full[cols] = weights
    active = weights - bench[cols]
    exposure = X.T @ weights - bench_exposure
    ex_ante = float(np.sqrt(max(active @ (D * active) + exposure @ snapshot.factor_cov @ exposure, 0.0) * 12))
    order = cols[np.argsort(-weights, kind="stable")]
    selected = np.array([c for c in order if full[c] > 0], dtype=np.int64)
    turnover = float(np.abs(weights - w0).sum() / 2)
    return ConstructionResult(full, selected, {
        "optimizer_level": level, "optimizer_level_index": FALLBACKS.index(level), "optimizer_status": status,
        "ex_ante_te": ex_ante, "candidates": int(len(cols)),
        "selected": int(len(selected)), "turnover": turnover, "invested": float(weights.sum())})
