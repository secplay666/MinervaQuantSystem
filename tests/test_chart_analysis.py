"""Automatic chart analysis on hand-built price paths with a known answer."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from quant_system.analytics.chart_analysis import (
    Series,
    analyse,
    average_true_range,
    candle_patterns,
    chart_patterns,
    support_resistance,
    trend_lines,
    zigzag,
)


def path(*waypoints: tuple[int, float], wiggle: float = 0.05) -> list[dict]:
    """Daily bars following straight lines between (bar, price) waypoints."""
    closes: list[float] = []
    for (i0, p0), (i1, p1) in zip(waypoints, waypoints[1:]):
        for k in range(i0, i1):
            closes.append(p0 + (p1 - p0) * (k - i0) / (i1 - i0))
    closes.append(waypoints[-1][1])
    start = dt.date(2024, 1, 1)
    bars, previous = [], closes[0]
    for k, close in enumerate(closes):
        high = max(previous, close) + wiggle
        low = min(previous, close) - wiggle
        bars.append({"trade_date": (start + dt.timedelta(days=k)).isoformat(), "open": previous, "high": high,
                     "low": low, "close": close, "volume": 1000.0})
        previous = close
    return bars


def run(bars: list[dict], multiple: float = 2.5):
    s = Series.from_bars(bars)
    atr = average_true_range(s)
    return s, atr, zigzag(s, multiple, atr)


def test_zigzag_finds_the_turning_points() -> None:
    s, _, pivots = run(path((0, 10), (20, 12), (40, 10.5), (60, 13), (80, 11)))
    assert [(p.kind, p.confirmed) for p in pivots] == [("L", True), ("H", True), ("L", True), ("H", True),
                                                       ("L", False)]
    assert [p.index for p in pivots[1:4]] == [20, 40, 60]
    assert pivots[3].price == pytest.approx(13.05)


def test_head_and_shoulders_top_is_confirmed_below_the_neckline() -> None:
    bars = path((0, 10), (15, 12), (25, 11), (40, 13), (50, 11), (65, 12), (80, 10))
    s, atr, pivots = run(bars)
    [pattern] = [p for p in chart_patterns(s, pivots, atr) if p.kind.startswith("head_shoulders")]
    assert pattern.kind == "head_shoulders_top" and pattern.status == "confirmed"
    assert [i for i, _ in pattern.points] == [15, 25, 40, 50, 65]
    assert pattern.target == pytest.approx(11 - 2, abs=0.2)  # neckline minus the head's height
    assert s.close[pattern.breakout_index] < 11


def test_double_bottom_forming_until_the_neckline_breaks() -> None:
    forming = path((0, 14), (20, 10), (35, 12), (55, 10.05), (62, 11.4))  # back up, still under the neckline
    s, atr, pivots = run(forming)
    kinds = {p.kind: p for p in chart_patterns(s, pivots, atr)}
    assert kinds["double_bottom"].status == "forming" and kinds["double_bottom"].breakout_index is None
    s, atr, pivots = run(path((0, 14), (20, 10), (35, 12), (55, 10.05), (70, 12.8)))
    [pattern] = [p for p in chart_patterns(s, pivots, atr) if p.kind == "double_bottom"]
    assert pattern.status == "confirmed" and pattern.direction == "bullish"
    assert pattern.target == pytest.approx(12.05 + (12.05 - 10.0), abs=0.3)


def test_ascending_triangle() -> None:
    bars = path((0, 9), (10, 12), (20, 10), (30, 12), (40, 10.8), (50, 12), (58, 11.3), (66, 13.2))
    s, atr, pivots = run(bars, multiple=2.0)
    names = [p.kind for p in chart_patterns(s, pivots, atr)]
    assert "ascending_triangle" in names


def test_bull_flag_after_a_pole() -> None:
    bars = path((0, 10), (8, 10.2), (16, 13.5), (28, 12.6), (34, 14.0))
    s, atr, pivots = run(bars)
    flags = [p for p in chart_patterns(s, pivots, atr) if p.kind in ("flag", "pennant")]
    assert flags and flags[0].direction == "bullish" and flags[0].status == "confirmed"


def test_support_and_resistance_where_swings_cluster() -> None:
    bars = path((0, 10), (15, 12), (30, 10), (45, 12.05), (60, 10.02), (75, 11.95), (90, 11))
    s, atr, pivots = run(bars)
    levels = support_resistance(s, pivots, atr)
    resistance = [lv for lv in levels if lv.kind == "resistance"]
    support = [lv for lv in levels if lv.kind == "support"]
    assert resistance[0].price == pytest.approx(12.05, abs=0.1) and resistance[0].touches == 3
    assert support[0].price == pytest.approx(10.0, abs=0.1) and support[0].touches >= 2


def test_rising_trend_line_through_higher_lows() -> None:
    bars = path((0, 10), (10, 11.5), (20, 10.6), (30, 12.2), (40, 11.2), (50, 12.9), (60, 11.8), (70, 13.4))
    s, atr, pivots = run(bars, multiple=2.0)
    up = [tl for tl in trend_lines(s, pivots, atr) if tl.kind == "up"]
    assert up and not up[0].broken and up[0].touches >= 3
    assert up[0].value(70) < s.low[70]  # still under price
    assert up[0].channel_offset and up[0].channel_offset > 0


def test_candlestick_patterns() -> None:
    bars = path((0, 12), (10, 10))  # a decline into the test bars
    last = bars[-1]["close"]

    def bar(o: float, h: float, low: float, c: float) -> dict:
        day = dt.date.fromisoformat(bars[-1]["trade_date"]) + dt.timedelta(days=1)
        return {"trade_date": day.isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": 1000.0}

    bars.append(bar(last, last + 0.02, last - 0.6, last - 0.1))  # hammer: long lower shadow
    found = {c.index: c for c in candle_patterns(Series.from_bars(bars))}
    assert found[len(bars) - 1].kind == "hammer" and found[len(bars) - 1].label == "锤"

    bars = path((0, 12), (10, 10))
    bars.append(bar(10.0, 10.05, 9.55, 9.6))  # bearish
    bars.append(bar(9.55, 10.2, 9.5, 10.15))  # engulfs it
    found = {c.index: c for c in candle_patterns(Series.from_bars(bars))}
    assert found[len(bars) - 1].kind == "bullish_engulfing"

    bars = path((0, 12), (10, 10))
    bars.append(bar(10.0, 10.02, 9.3, 9.35))  # long bearish
    bars.append(bar(9.3, 9.36, 9.2, 9.28))  # small body at the bottom
    bars.append(bar(9.3, 9.9, 9.28, 9.85))  # bullish, closes above the first body's middle
    found = {c.index: c for c in candle_patterns(Series.from_bars(bars))}
    assert found[len(bars) - 1].kind == "morning_star" and found[len(bars) - 1].bars == 3

    one_price = path((0, 12), (10, 10))
    one_price.append(bar(11.0, 11.0, 11.0, 11.0))  # limit up all day
    assert not [c for c in candle_patterns(Series.from_bars(one_price)) if c.index == len(one_price) - 1]


def test_analyse_returns_dates_and_is_deterministic() -> None:
    bars = path((0, 10), (15, 12), (25, 11), (40, 13), (50, 11), (65, 12), (80, 10))
    first, second = analyse(bars), analyse(bars)
    assert first == second and first["bars"] == len(bars)
    assert {"pivots", "levels", "trendlines", "fibonacci", "patterns", "candles"} <= set(first)
    pattern = next(p for p in first["patterns"] if p["kind"] == "head_shoulders_top")
    assert pattern["points"][2]["date"] == bars[40]["trade_date"] and pattern["status"] == "confirmed"
    assert first["fibonacci"]["levels"][4]["ratio"] == 0.618
    assert analyse(bars[:10])["patterns"] == []
    assert np.isfinite(first["atr"])
