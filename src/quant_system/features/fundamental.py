"""Fundamental factors (value, quality, growth) from point-in-time statements.

Inputs come from ``research.fundamentals.asof`` (usable at the close of the
row's session, ADR-004) and the total market cap at that close.  Without
fundamentals the factors are all missing.  Banks, brokers and insurers have
no operating cost, so gross margin is missing for them; leverage is
industry-neutralized like every factor.
"""

from __future__ import annotations

import numpy as np

from .context import FactorContext
from .registry import factor

NEEDS = ("fundamentals",)


def _field(ctx: FactorContext, name: str, rows: np.ndarray) -> np.ndarray:
    store = ctx.research.fundamentals
    if store is None:
        return np.full((len(rows), ctx.shape[1]), np.nan)
    return ctx.memo(f"fund_{name}_{hash(rows.tobytes())}", lambda: store.asof(name, rows))


def _per_cap(ctx: FactorContext, name: str, rows: np.ndarray) -> np.ndarray:
    cap = ctx.total_mcap()[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cap > 0, _field(ctx, name, rows) / cap, np.nan)


def _ratio(numerator: np.ndarray, denominator: np.ndarray, positive: bool = True) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        valid = denominator > 0 if positive else denominator != 0
        return np.where(valid, numerator / denominator, np.nan)


# -- value --------------------------------------------------------------------------

@factor("ep_ttm", "value", "归母净利润（TTM）/ 总市值", +1, 1, requires=NEEDS)
def ep_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _per_cap(ctx, "ni_ttm", rows)


@factor("bp", "value", "归母净资产（扣除其他权益工具）/ 总市值", +1, 1, requires=NEEDS)
def bp(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _per_cap(ctx, "book", rows)


@factor("sp_ttm", "value", "营业收入（TTM）/ 总市值", +1, 1, requires=NEEDS)
def sp_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _per_cap(ctx, "revenue_ttm", rows)


@factor("cfp_ttm", "value", "经营活动现金流净额（TTM）/ 总市值", +1, 1, requires=NEEDS)
def cfp_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _per_cap(ctx, "ocf_ttm", rows)


@factor("dy_ttm", "value", "近 12 个月现金分红 / 总市值", +1, 1, requires=NEEDS)
def dy_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    cap = ctx.total_mcap()[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cap > 0, ctx.research.dividend_ttm_cny[rows] / cap, np.nan)


# -- quality ------------------------------------------------------------------------

@factor("roe_ttm", "quality", "归母净利润（TTM）/ 平均归母净资产", +1, 1, requires=NEEDS)
def roe_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _ratio(_field(ctx, "ni_ttm", rows), _field(ctx, "avg_book", rows))


@factor("roa_ttm", "quality", "归母净利润（TTM）/ 平均总资产", +1, 1, requires=NEEDS)
def roa_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _ratio(_field(ctx, "ni_ttm", rows), _field(ctx, "avg_assets", rows))


@factor("gross_margin_ttm", "quality", "毛利率（TTM）", +1, 1, requires=NEEDS)
def gross_margin_ttm(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _ratio(_field(ctx, "gross_profit_ttm", rows), _field(ctx, "revenue_ttm", rows))


@factor("accruals", "quality", "应计（(净利润 - 经营现金流) TTM / 平均总资产）", -1, 1, requires=NEEDS)
def accruals(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _ratio(_field(ctx, "ni_ttm", rows) - _field(ctx, "ocf_ttm", rows), _field(ctx, "avg_assets", rows))


@factor("leverage", "quality", "资产负债率", -1, 1, requires=NEEDS)
def leverage(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _ratio(_field(ctx, "total_liabilities", rows), _field(ctx, "total_assets", rows))


# -- growth -------------------------------------------------------------------------

@factor("ni_yoy", "growth", "归母净利润单季同比增速", +1, 1, requires=NEEDS)
def ni_yoy(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _field(ctx, "ni_sq_yoy", rows)


@factor("rev_yoy", "growth", "营业收入单季同比增速", +1, 1, requires=NEEDS)
def rev_yoy(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _field(ctx, "rev_sq_yoy", rows)


@factor("dni_yoy", "growth", "扣非归母净利润单季同比增速", +1, 1, requires=NEEDS)
def dni_yoy(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _field(ctx, "dni_sq_yoy", rows)
