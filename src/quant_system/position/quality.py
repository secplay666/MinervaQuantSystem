"""The quality grade of a stock (docs/design/position-manager.md §11.6).

Six dimensions scored 0-100, the grade from their average.  It orders lists
and nothing else: no rule reads it (the methodology's iron rule).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

DIMENSIONS = ("peg", "revenue_growth", "profit_growth", "roe", "margin_trend", "forecast")
NAMES = {"peg": "PEG", "revenue_growth": "营收同比", "profit_growth": "净利同比", "roe": "ROE",
         "margin_trend": "毛利率趋势", "forecast": "业绩预告"}
FORECAST_SCORES = {"预增": 100, "扭亏": 100, "略增": 75, "续盈": 75, "减亏": 60, "不确定": 50, "略减": 30,
                   "预减": 10, "首亏": 10, "续亏": 10, "增亏": 10}
GRADES = ((75, "A"), (60, "B"), (40, "C"))
MIN_DIMENSIONS = 3


def _steps(value: float, steps: Sequence[tuple[float, float]], floor: float) -> float:
    """The score of the first threshold ``value`` reaches (thresholds descending)."""
    for threshold, score in steps:
        if value >= threshold:
            return score
    return floor


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def growth_score(growth: float | None) -> float | None:
    if not _finite(growth):
        return None
    return _steps(growth, ((0.30, 100), (0.15, 80), (0.05, 60), (0.0, 45), (-0.20, 25)), 10)


def peg_score(pe: float | None, growth: float | None) -> float | None:
    """PEG = PE (TTM) / (growth x 100); a loss or shrinking profit scores 15."""
    if not (_finite(pe) and _finite(growth)):
        return None
    if pe <= 0 or growth <= 0:
        return 15
    peg = pe / (growth * 100)
    for limit, score in ((0.5, 100), (1.0, 80), (1.5, 60), (2.5, 40)):
        if peg < limit:
            return score
    return 15


def roe_score(roe: float | None) -> float | None:
    if not _finite(roe):
        return None
    return _steps(roe, ((0.20, 100), (0.15, 85), (0.10, 70), (0.05, 50), (0.0, 30)), 10)


def margin_trend_score(change_points: float | None) -> float | None:
    """The change of the TTM gross margin over a year, in percentage points."""
    if not _finite(change_points):
        return None
    return _steps(change_points, ((3, 100), (1, 80), (-1, 60), (-3, 40)), 20)


def forecast_score(kind: str | None) -> float | None:
    return FORECAST_SCORES.get(kind) if kind else None


def grade(values: dict[str, float | None], forecast: str | None) -> dict:
    """``values``: pe, profit_growth, revenue_growth, roe, margin_change (points).
    Returns {grade, score, dims: {name: {value, score}}}; no grade below MIN_DIMENSIONS."""
    pe, profit = values.get("pe"), values.get("profit_growth")
    peg = pe / (profit * 100) if _finite(pe) and _finite(profit) and pe > 0 and profit > 0 else None
    dims = {
        "peg": (peg, peg_score(pe, profit)),
        "revenue_growth": (values.get("revenue_growth"), growth_score(values.get("revenue_growth"))),
        "profit_growth": (values.get("profit_growth"), growth_score(values.get("profit_growth"))),
        "roe": (values.get("roe"), roe_score(values.get("roe"))),
        "margin_trend": (values.get("margin_change"), margin_trend_score(values.get("margin_change"))),
        "forecast": (forecast, forecast_score(forecast)),
    }
    scores = [s for _, s in dims.values() if s is not None]
    total = sum(scores) / len(scores) if len(scores) >= MIN_DIMENSIONS else None
    letter = None if total is None else next((g for limit, g in GRADES if total >= limit), "D")
    out = {"grade": letter, "score": None if total is None else round(total, 1),
           "dims": {name: {"name": NAMES[name], "value": None if isinstance(v, float) and not math.isfinite(v) else v,
                           "score": s} for name, (v, s) in dims.items()}}
    out["dims"]["peg"]["pe"] = pe if _finite(pe) else None  # shown beside the PEG (a loss has no PEG)
    return out
