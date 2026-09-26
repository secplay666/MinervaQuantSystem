"""Causal rolling statistics on [T, N] panels.

Every statistic at row t uses rows (t - window, t] only, so it is safe to
compute over the whole panel and read the rows of interest.  Missing
values (NaN) are skipped; a statistic is NaN when fewer than ``min_count``
observations fall in the window, and for the first ``window - 1`` rows.
Cumulative sums are float64; the inputs used here (returns, turnover
rates) are small enough that cancellation is not a concern.
"""

from __future__ import annotations

import numpy as np


def _windowed(values: np.ndarray, window: int) -> np.ndarray:
    """Sum over (t - window, t] of a NaN-free panel; NaN before a full window."""
    total = np.zeros((values.shape[0] + 1,) + values.shape[1:], dtype=np.float64)
    np.cumsum(values, axis=0, out=total[1:])
    out = np.full(values.shape, np.nan)
    if values.shape[0] >= window:
        out[window - 1:] = total[window:] - total[:-window]
    return out


def rolling_count(valid: np.ndarray, window: int) -> np.ndarray:
    return _windowed(valid.astype(np.float64), window)


def rolling_sum(x: np.ndarray, window: int, min_count: int) -> np.ndarray:
    valid = np.isfinite(x)
    count = rolling_count(valid, window)
    total = _windowed(np.where(valid, x, 0.0), window)
    return np.where(count >= min_count, total, np.nan)


def rolling_mean(x: np.ndarray, window: int, min_count: int) -> np.ndarray:
    valid = np.isfinite(x)
    count = rolling_count(valid, window)
    total = _windowed(np.where(valid, x, 0.0), window)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count >= min_count, total / count, np.nan)


def rolling_std(x: np.ndarray, window: int, min_count: int, ddof: int = 1) -> np.ndarray:
    valid = np.isfinite(x)
    count = rolling_count(valid, window)
    values = np.where(valid, x, 0.0)
    total = _windowed(values, window)
    squares = _windowed(values * values, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        variance = (squares - total * total / count) / (count - ddof)
    return np.where(count >= max(min_count, ddof + 1), np.sqrt(np.maximum(variance, 0.0)), np.nan)


def rolling_cov_corr(x: np.ndarray, y: np.ndarray, window: int, min_count: int) -> tuple[np.ndarray, ...]:
    """(cov, corr, var_x, var_y) over pairwise-complete observations."""
    x, y = np.broadcast_arrays(x, y)
    valid = np.isfinite(x) & np.isfinite(y)
    count = rolling_count(valid, window)
    xs, ys = np.where(valid, x, 0.0), np.where(valid, y, 0.0)
    sx, sy = _windowed(xs, window), _windowed(ys, window)
    sxx, syy, sxy = _windowed(xs * xs, window), _windowed(ys * ys, window), _windowed(xs * ys, window)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = (sxy - sx * sy / count) / (count - 1)
        var_x = (sxx - sx * sx / count) / (count - 1)
        var_y = (syy - sy * sy / count) / (count - 1)
        corr = cov / np.sqrt(var_x * var_y)
    enough = count >= max(min_count, 3)
    nan = np.nan
    return (np.where(enough, cov, nan), np.where(enough & (var_x > 0) & (var_y > 0), corr, nan),
            np.where(enough, var_x, nan), np.where(enough, var_y, nan))


def rolling_max_at(x: np.ndarray, rows: np.ndarray, window: int, min_count: int) -> np.ndarray:
    """Max over (t - window, t] for the requested rows only."""
    out = np.full((len(rows), x.shape[1]), np.nan)
    for k, t in enumerate(rows):
        if t < window - 1:
            continue
        block = x[t - window + 1:t + 1]
        count = np.isfinite(block).sum(axis=0)
        best = np.max(np.where(np.isfinite(block), block, -np.inf), axis=0)
        out[k] = np.where(count >= min_count, best, np.nan)
    return out


def lagged(x: np.ndarray, lag: int) -> np.ndarray:
    """x[t - lag] at row t (NaN for the first ``lag`` rows)."""
    out = np.full(x.shape, np.nan)
    if lag == 0:
        return x.astype(np.float64, copy=True)
    out[lag:] = x[:-lag]
    return out
