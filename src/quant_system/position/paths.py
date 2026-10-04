"""Around a structure (docs/design/position-manager.md §11): the base zone's
automatic 左侧→筑底, sentinels, the path suggestion when a round is over, and
how far a price is in days.  Pure functions of closes and confirmed levels
(hfq prices); like the rules, nothing here moves the simulated position."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

from .rules import LEFT, Event, Outcome, Structure, label_on

PATH_NAMES = {"A": "浅回撤候买回", "B": "深蹲候形态", "C": "新高突破"}


@dataclass(frozen=True)
class BaseZone:
    """The 起涨区 of path B (lower may be missing: then only the upper edge counts)."""
    upper: float
    lower: float | None
    effective: date
    version: int = 1


@dataclass(frozen=True)
class Sentinel:
    id: int
    price: float
    direction: str          # up: a close at or above | down: a close at or below
    effective: date
    source: str | None = None
    version: int = 1        # bumped by every reset (the same day included), so a reset can fire again


def _sessions(dates: list[date], closes: np.ndarray, start: date):
    for day, close in zip(dates, closes):
        if day >= start and np.isfinite(close):
            yield day, float(close)


def base_zone_entry(dates: list[date], closes: np.ndarray, labels: list[tuple[date, str]], zone: BaseZone,
                    auto: bool = True) -> Event | None:
    """The first close at or below the zone's upper edge while the label is 左侧
    (the only automatic label change, design §11.1)."""
    for day, close in _sessions(dates, closes, zone.effective):
        if label_on(labels, day) == LEFT and close <= zone.upper:
            message = ("收盘进入起涨区，标签自动转为筑底，开始盯形态" if auto
                       else "收盘进入起涨区，开始盯形态（自动转筑底已关闭）")
            return Event(day, "base_zone", 4, message, close, 0.0, None, f"base_zone:v{zone.version}")
    return None


def sentinel_crossing(dates: list[date], closes: np.ndarray, sentinel: Sentinel) -> Event | None:
    """The first close through the sentinel; it only reminds (never changes the label)."""
    up = sentinel.direction == "up"
    for day, close in _sessions(dates, closes, sentinel.effective):
        if (close >= sentinel.price) if up else (close <= sentinel.price):
            what = f"（{sentinel.source}）" if sentinel.source else ""
            return Event(day, "sentinel", 2, f"收盘{'站上' if up else '跌到'}哨兵{what}，是否更新方向标签？", close, 0.0,
                         None, f"sentinel:{sentinel.id}:v{sentinel.version}")
    return None


def mean_range(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 20) -> float | None:
    """Mean daily range (high - low) / previous close over the last ``window`` sessions."""
    if len(close) < 2:
        return None
    previous = close[:-1][-window:]
    ranges = (high[1:] - low[1:])[-window:] / previous
    ranges = ranges[np.isfinite(ranges) & (ranges > 0)]
    return float(ranges.mean()) if len(ranges) else None


def days_away(price: float, close: float, daily_range: float | None) -> int | None:
    """The distance to ``price`` in days of an average range ("约 N 天路程")."""
    if not daily_range or not close:
        return None
    return max(1, math.ceil(round(abs(price / close - 1) / daily_range, 6)))  # 0.09 / 0.03 is 3, not 4


def suggest_path(structure: Structure, outcome: Outcome, close: float | None) -> dict | None:
    """When the round is over, which of the three paths the last close points to
    (design §11.1), with prices to pre-fill (hfq).  The user decides."""
    if not outcome.round_over or outcome.peak_close is None or close is None:
        return None
    peak, neck = outcome.peak_close, structure.neckline
    height = peak - neck
    pullback = (peak - close) / height if height > 0 else 1.0
    if close >= peak * 0.95:
        return {"path": "C", "name": PATH_NAMES["C"], "pullback": pullback,
                "reason": f"收盘在本轮峰值的 {close / peak:.0%}，只整理不深跌，买点在上方",
                "levels": {"neckline": peak}}
    if pullback <= 0.5:
        return {"path": "A", "name": PATH_NAMES["A"], "pullback": pullback,
                "reason": f"自峰值回撤了涨幅的 {pullback:.0%}，大结构未坏",
                "levels": {"buyback_zone": (peak - 0.5 * height, peak - 0.382 * height), "fraction": 0.5}}
    return {"path": "B", "name": PATH_NAMES["B"], "pullback": pullback,
            "reason": f"自峰值回撤了涨幅的 {pullback:.0%}，回到起涨区附近要重新筑底",
            "levels": {"base_zone": (neck, neck * 1.1), "target": peak}}
