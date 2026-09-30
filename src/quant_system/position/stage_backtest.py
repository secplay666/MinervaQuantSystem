"""How the four stages performed on the whole A-share market (the 阶段回测 page).

For a parameter set: stage per stock per day on hfq-adjusted closes, then
* forward excess returns (20/60/120 sessions, against the equal-weighted
  market) per stage and after each stage change;
* base -> advance entries by the CSI 300's own stage (the index gate) and
  with the volume criterion ②;
* a long-only book of the stocks in stage 2, with and without the index
  gate, per year and overall (return, maximum drawdown, names held).
Survivorship-free: delisted stocks count while they traded.
"""

from __future__ import annotations

import warnings
from datetime import date

import numpy as np
import pandas as pd

from ..backtest.market_data import MarketData
from .stages import ADVANCE, BASE, DECLINE, STAGE_KEYS, STAGE_NAMES, TOP, StageParams, classify, volume_surge

HORIZONS = (20, 60, 120)
TRANSITIONS = ((BASE, ADVANCE), (TOP, ADVANCE), (ADVANCE, TOP), (TOP, DECLINE), (DECLINE, BASE), (BASE, DECLINE))
MIN_HISTORY = 250  # a stock counts after a year of bars


def _forward(price: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
    fwd = np.full_like(price, np.nan)
    fwd[:-h] = price[h:] / price[:-h] - 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # sessions where no stock has a forward return
        market = np.nanmean(fwd, axis=1, keepdims=True)
    return fwd, fwd - market


def _stats(sel: np.ndarray, fwd: dict) -> dict:
    out = {"count": int(sel.sum())}
    for h in HORIZONS:
        raw, excess = fwd[h]
        out[f"excess_{h}"] = _mean(excess[sel])
        out[f"return_{h}"] = _mean(raw[sel])
    values = fwd[60][1][sel]
    values = values[np.isfinite(values)]
    out["beat_60"] = float((values > 0).mean()) if len(values) else None
    return out


def _mean(values: np.ndarray) -> float | None:
    values = values[np.isfinite(values)]
    return float(values.mean()) if len(values) else None


def run_stage_backtest(market: MarketData, index_close: np.ndarray, params: StageParams,
                       start: date | None = None, end: date | None = None) -> dict:
    """``index_close``: the CSI 300 aligned with ``market.sessions`` (NaN where missing)."""
    sessions = np.array(market.sessions, dtype="datetime64[D]")
    close = np.where(market.has_bar, market.close / 100.0, np.nan)
    price = pd.DataFrame(close * market.hfq).ffill().to_numpy()
    volume = np.where(market.has_bar, market.volume, np.nan)
    open_ = np.where(market.has_bar, market.open / 100.0, np.nan)
    listed = market.bar_count >= MIN_HISTORY
    first = int(np.searchsorted(sessions, np.datetime64(start))) if start else params.ma + params.slope
    last = int(np.searchsorted(sessions, np.datetime64(end), side="right")) if end else len(sessions)
    window = np.zeros(len(sessions), dtype=bool)
    window[max(first, params.ma + params.slope):last] = True
    mask = listed & window[:, None]

    stage = classify(price, params)
    index_price = pd.Series(index_close).ffill().to_numpy()
    index_stage = classify(index_price, params.for_index())
    fwd = {h: _forward(price, h) for h in HORIZONS}
    prev = np.vstack([np.zeros((1, stage.shape[1]), dtype=np.int8), stage[:-1]])

    total = int((mask & (stage > 0)).sum()) or 1
    stages = [{"stage": STAGE_KEYS[code], "name": STAGE_NAMES[code],
               "share": float((mask & (stage == code)).sum() / total), **_stats(mask & (stage == code), fwd)}
              for code in (BASE, ADVANCE, TOP, DECLINE)]
    transitions = [{"from": STAGE_KEYS[a], "to": STAGE_KEYS[b], "name": f"{STAGE_NAMES[a]}→{STAGE_NAMES[b]}",
                    **_stats(mask & (prev == a) & (stage == b), fwd)} for a, b in TRANSITIONS]

    entries = mask & (prev == BASE) & (stage == ADVANCE)
    surge = np.zeros_like(entries)
    for j in np.flatnonzero(entries.any(axis=0)):
        hit = volume_surge(close[:, j], open_[:, j], volume[:, j], params)
        surge[:, j] = pd.Series(hit).rolling(params.dry_window, min_periods=1).max().to_numpy() > 0
    gate = [{"index_stage": STAGE_KEYS[code], "name": f"沪深300{STAGE_NAMES[code]}",
             **_stats(entries & (index_stage == code)[:, None], fwd)} for code in (BASE, ADVANCE, TOP, DECLINE)]
    filters = [
        {"name": "全部筑底→右侧", **_stats(entries, fwd)},
        {"name": "沪深300 不在左侧", **_stats(entries & (index_stage != DECLINE)[:, None], fwd)},
        {"name": "缩量后放量阳线（判据②）", **_stats(entries & surge, fwd)},
        {"name": "判据②且沪深300 不在左侧", **_stats(entries & surge & (index_stage != DECLINE)[:, None], fwd)},
    ]

    daily = np.full_like(price, np.nan)
    daily[1:] = price[1:] / price[:-1] - 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        bench = np.nan_to_num(np.nanmean(np.where(listed, daily, np.nan), axis=1))
    books = {"stage2": (stage == ADVANCE) & listed}
    books["stage2_gated"] = books["stage2"] & (index_stage == ADVANCE)[:, None]
    returns = {}
    for key, book in books.items():
        held = book[:-1].astype(float)
        n = held.sum(axis=1)
        r = np.zeros(len(sessions))
        r[1:] = np.where(n > 0, np.nansum(held * np.nan_to_num(daily[1:]), axis=1) / np.maximum(n, 1), 0.0)
        returns[key] = (r, n)
    years = pd.DatetimeIndex(sessions).year.to_numpy()
    yearly = []
    for year in sorted(set(years[window])):
        sel = window & (years == year)
        if not listed[sel].any():  # before any stock has a year of history
            continue
        share = {STAGE_KEYS[c]: float(np.mean(index_stage[sel] == c)) for c in (BASE, ADVANCE, TOP, DECLINE)}
        yearly.append({"year": int(year), "market": float(np.prod(1 + bench[sel]) - 1),
                       **{key: float(np.prod(1 + r[sel]) - 1) for key, (r, _) in returns.items()},
                       "index_stages": share})
    overall = {"market": _book(bench[window])}
    for key, (r, n) in returns.items():
        overall[key] = {**_book(r[window]), "avg_names": float(np.mean(n[window[1:]])) if window[1:].any() else 0.0}

    return {"params": params.to_dict(), "start": str(sessions[window][0]) if window.any() else None,
            "end": str(sessions[window][-1]) if window.any() else None, "stocks": int(listed[window].any(axis=0).sum()),
            "stages": stages, "transitions": transitions, "gate": gate, "filters": filters, "yearly": yearly,
            "overall": overall}


def _book(r: np.ndarray) -> dict:
    if not len(r):
        return {"total": None, "max_drawdown": None}
    wealth = np.cumprod(1 + r)
    return {"total": float(wealth[-1] - 1), "max_drawdown": float((wealth / np.maximum.accumulate(wealth) - 1).min())}
