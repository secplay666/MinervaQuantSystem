"""Price and volume factors (momentum, volatility, liquidity, size, technical).

Directions follow the usual A-share priors and are hypotheses to be tested
in-sample, not results: e.g. short-term reversal, low volatility and
illiquidity are expected to be rewarded.
"""

from __future__ import annotations

import numpy as np

from .context import FactorContext
from .registry import factor
from .rolling import lagged, rolling_cov_corr, rolling_max_at, rolling_mean, rolling_std

YEAR, HALF_YEAR, MONTH = 240, 120, 20
MIN_COVERAGE = 0.8  # share of sessions with a bar required inside a lookback window


def _covered(ctx: FactorContext, values: np.ndarray, rows: np.ndarray, window: int) -> np.ndarray:
    coverage = ctx.bar_coverage(window)[rows]
    return np.where(coverage >= MIN_COVERAGE, values, np.nan)


def _price_ratio(ctx: FactorContext, rows: np.ndarray, newer: int, older: int) -> np.ndarray:
    adj = ctx.adj_last()
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = lagged(adj, newer)[rows] / lagged(adj, older)[rows] - 1.0
    return _covered(ctx, ratio, rows, older + 1)


# -- momentum ---------------------------------------------------------------------

@factor("mom_12_1", "momentum", "12 个月动量（跳过最近 1 个月）", +1, YEAR + 1)
def mom_12_1(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _price_ratio(ctx, rows, MONTH, YEAR)


@factor("mom_6_1", "momentum", "6 个月动量（跳过最近 1 个月）", +1, HALF_YEAR + 1)
def mom_6_1(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _price_ratio(ctx, rows, MONTH, HALF_YEAR)


@factor("rev_1m", "momentum", "1 个月收益（短期反转）", -1, MONTH + 1)
def rev_1m(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return _price_ratio(ctx, rows, 0, MONTH)


@factor("high_52w", "momentum", "收盘价 / 52 周最高收盘价", +1, 244)
def high_52w(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    peak = rolling_max_at(ctx.adj_close(), rows, 244, 180)
    with np.errstate(invalid="ignore", divide="ignore"):
        return ctx.adj_last()[rows] / peak


# -- volatility -------------------------------------------------------------------

@factor("vol_60", "volatility", "60 日收益波动率", -1, 60)
def vol_60(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return ctx.memo("vol_60", lambda: rolling_std(ctx.returns(), 60, 40))[rows]


@factor("ivol_60", "volatility", "60 日特质波动率（相对全市场等权收益）", -1, 60)
def ivol_60(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    def build() -> np.ndarray:
        cov, _, var_r, var_m = rolling_cov_corr(ctx.returns(), ctx.market_returns()[:, None], 60, 40)
        with np.errstate(invalid="ignore", divide="ignore"):
            residual = var_r - cov * cov / var_m
        return np.sqrt(np.maximum(residual, 0.0))

    return ctx.memo("ivol_60", build)[rows]


@factor("max_20", "volatility", "20 日最大单日收益", -1, 20)
def max_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return rolling_max_at(ctx.returns(), rows, 20, 15)


# -- liquidity --------------------------------------------------------------------

@factor("turn_20", "liquidity", "20 日平均换手率", -1, 20)
def turn_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return ctx.memo("turn_20", lambda: rolling_mean(ctx.turnover_rate(), 20, 15))[rows]


@factor("abn_turn", "liquidity", "异常换手（20 日 / 250 日平均换手率）", -1, 250)
def abn_turn(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    long = ctx.memo("turn_250", lambda: rolling_mean(ctx.turnover_rate(), 250, 150))[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        return turn_20(ctx, rows) / long


@factor("amihud_20", "liquidity", "Amihud 非流动性（|收益| / 成交额，20 日均值）", +1, 20)
def amihud_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    def build() -> np.ndarray:
        amount = ctx.turnover_cny()
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(amount > 0, np.abs(ctx.returns()) / amount * 1e8, np.nan)
        return rolling_mean(ratio, 20, 15)

    return ctx.memo("amihud_20", build)[rows]


# -- size ---------------------------------------------------------------------------

@factor("ln_float_mcap", "size", "流通市值对数", -1, 1, neutralize=("industry",))
def ln_float_mcap(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    cap = ctx.float_mcap()[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cap > 0, np.log(cap), np.nan)


# -- technical ----------------------------------------------------------------------

@factor("amplitude_20", "technical", "20 日平均振幅（(最高-最低)/前收）", -1, 20)
def amplitude_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    def build() -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            daily = (ctx.high() - ctx.low()) / ctx.reference_close()
        return rolling_mean(daily, 20, 15)

    return ctx.memo("amplitude_20", build)[rows]


@factor("pv_corr_20", "technical", "20 日价量相关系数（复权收盘价与成交量）", -1, 20)
def pv_corr_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return ctx.memo("pv_corr_20", lambda: rolling_cov_corr(ctx.adj_close(), ctx.volume(), 20, 15)[1])[rows]


@factor("turn_vol_20", "technical", "20 日换手率波动", -1, 20)
def turn_vol_20(ctx: FactorContext, rows: np.ndarray) -> np.ndarray:
    return ctx.memo("turn_vol_20", lambda: rolling_std(ctx.turnover_rate(), 20, 15))[rows]
