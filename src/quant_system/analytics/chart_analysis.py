"""Automatic chart analysis for the stock chart (看盘).

Swing points, support and resistance zones, trend lines and channels, the
latest Fibonacci leg, classic chart patterns and candlestick patterns, all
computed from one bar series (the chart's own period and adjustment) with
fixed rules, so results are reproducible and testable.

The rules follow the usual definitions: Edwards & Magee and Murphy for
support, resistance and trend lines; Bulkowski's Encyclopedia of Chart
Patterns for shapes and tolerances; Lo, Mamaysky & Wang (2000) for defining
patterns on successive extrema.  Swings are found with an ATR-scaled zigzag,
so one sensitivity setting suits volatile stocks and quiet indices alike.

The last swing point is provisional until price has reversed by the
threshold; anything that depends on it is reported as not yet confirmed.
This is an aid for reading charts, not a trading signal.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

SENSITIVITY = {"fine": 1.5, "medium": 2.5, "coarse": 4.0}  # ATR multiples that make a swing
ATR_WINDOW = 14
RECENT_BARS = 120  # patterns must end this close to the last bar


@dataclass(frozen=True)
class Series:
    dates: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    @classmethod
    def from_bars(cls, bars: list[dict[str, Any]]) -> Series:
        def column(key: str) -> np.ndarray:
            return np.array([float(b.get(key) or 0.0) for b in bars], dtype=np.float64)

        return cls([str(b["trade_date"]) for b in bars], column("open"), column("high"), column("low"),
                   column("close"), column("volume"))

    def __len__(self) -> int:
        return len(self.dates)


def average_true_range(s: Series, window: int = ATR_WINDOW) -> np.ndarray:
    """Simple moving average of the true range; the first bars use what exists."""
    previous = np.concatenate([[s.close[0]], s.close[:-1]]) if len(s) else s.close
    true_range = np.maximum(s.high - s.low, np.maximum(abs(s.high - previous), abs(s.low - previous)))
    sums = np.cumsum(true_range)
    out = np.empty_like(true_range)
    for i in range(len(true_range)):
        start = max(0, i - window + 1)
        out[i] = (sums[i] - (sums[start - 1] if start else 0.0)) / (i - start + 1)
    return np.maximum(out, 1e-9)


# -- swing points --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str  # H | L
    confirmed: bool


def zigzag(s: Series, multiple: float, atr: np.ndarray | None = None) -> list[Pivot]:
    """Alternating swing highs and lows: a swing ends when price reverses from
    its extreme by ``multiple`` ATRs.  The last extreme is provisional."""
    n = len(s)
    if n < 3:
        return []
    atr = average_true_range(s) if atr is None else atr
    pivots: list[Pivot] = []
    up: bool | None = None
    hi = lo = 0
    for i in range(1, n):
        threshold = multiple * atr[i]
        if up is None:
            hi = i if s.high[i] > s.high[hi] else hi
            lo = i if s.low[i] < s.low[lo] else lo
            if s.high[hi] - s.low[lo] >= threshold:
                if hi > lo:
                    pivots.append(Pivot(lo, float(s.low[lo]), "L", True))
                    up = True
                else:
                    pivots.append(Pivot(hi, float(s.high[hi]), "H", True))
                    up = False
            continue
        if up:
            if s.high[i] > s.high[hi]:  # strict: the first bar of a tie is the swing point
                hi = i
            elif s.high[hi] - s.low[i] >= threshold:
                pivots.append(Pivot(hi, float(s.high[hi]), "H", True))
                up = False
                lo = hi + 1 + int(np.argmin(s.low[hi + 1:i + 1]))
        else:
            if s.low[i] < s.low[lo]:
                lo = i
            elif s.high[i] - s.low[lo] >= threshold:
                pivots.append(Pivot(lo, float(s.low[lo]), "L", True))
                up = True
                hi = lo + 1 + int(np.argmax(s.high[lo + 1:i + 1]))
    if up is True:
        pivots.append(Pivot(hi, float(s.high[hi]), "H", False))
    elif up is False:
        pivots.append(Pivot(lo, float(s.low[lo]), "L", False))
    return pivots


# -- support and resistance -------------------------------------------------------------------------


@dataclass
class Level:
    kind: str  # support | resistance
    low: float
    high: float
    price: float
    touches: int
    first_index: int
    last_index: int
    score: float
    flipped: bool  # held as both a high and a low: support turned resistance or back
    extreme: bool = False  # the window's highest high or lowest low


def support_resistance(s: Series, pivots: list[Pivot], atr: np.ndarray, per_side: int = 3) -> list[Level]:
    """Zones where two or more swing points cluster (within about 0.8 ATR),
    scored by touches and recency, plus the window's high and low.  The
    strongest ``per_side`` below and above the last close are kept."""
    n = len(s)
    confirmed = sorted((p for p in pivots if p.confirmed), key=lambda p: p.price)
    if not confirmed or n == 0:
        return []
    last_close = float(s.close[-1])
    tolerance = max(0.8 * float(atr[-1]), 0.005 * last_close)
    clusters: list[list[Pivot]] = []
    for pivot in confirmed:
        if clusters and pivot.price - clusters[-1][0].price <= 2 * tolerance:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    levels = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        indices = [p.index for p in cluster]
        weight = sum(0.5 + 0.5 * math.exp(-(n - 1 - i) / max(n / 3, 1)) for i in indices)
        flipped = len({p.kind for p in cluster}) == 2
        price = float(np.mean([p.price for p in cluster]))
        low, high = min(p.price for p in cluster), max(p.price for p in cluster)
        pad = max(0.0, (tolerance * 0.5 - (high - low)) / 2)
        levels.append(Level("resistance" if price > last_close else "support", low - pad, high + pad, price,
                            len(cluster), min(indices), max(indices), round(weight + (0.5 if flipped else 0.0), 3),
                            flipped))
    for index, price, kind in ((int(np.argmax(s.high)), float(s.high.max()), "resistance"),
                               (int(np.argmin(s.low)), float(s.low.min()), "support")):
        if not any(level.low - tolerance <= price <= level.high + tolerance for level in levels):
            levels.append(Level(kind, price - tolerance * 0.25, price + tolerance * 0.25, price, 1, index, index,
                                0.9, False, True))
    below = sorted((lv for lv in levels if lv.kind == "support"), key=lambda lv: -lv.score)[:per_side]
    above = sorted((lv for lv in levels if lv.kind == "resistance"), key=lambda lv: -lv.score)[:per_side]
    return sorted(below + above, key=lambda lv: lv.price)


# -- trend lines --------------------------------------------------------------------------------------


@dataclass
class TrendLine:
    kind: str  # up (support under rising lows) | down (resistance over falling highs)
    i1: int
    p1: float
    i2: int
    p2: float
    touches: int
    end_index: int  # the last bar, or where it broke
    broken: bool
    score: float
    channel_offset: float | None = None  # distance of the parallel line through the opposite extreme

    def value(self, index: float) -> float:
        return self.p1 + (self.p2 - self.p1) / (self.i2 - self.i1) * (index - self.i1)


def trend_lines(s: Series, pivots: list[Pivot], atr: np.ndarray, candidates: int = 8) -> list[TrendLine]:
    """The best rising support line through two confirmed swing lows and the
    best falling resistance line through two swing highs: no bar between the
    anchors crosses it by more than half an ATR; more touches, a longer span
    and being unbroken score higher.  A broken line ends at the break."""
    n = len(s)
    if n < 10:
        return []
    tolerance = 0.5 * float(atr[-1])
    out = []
    for kind in ("up", "down"):
        points = [p for p in pivots if p.confirmed and p.kind == ("L" if kind == "up" else "H")][-candidates:]
        best: TrendLine | None = None
        for a_pos, a in enumerate(points):
            for b in points[a_pos + 1:]:
                if b.index - a.index < 3 or (kind == "up" and b.price <= a.price) or (kind == "down" and b.price >= a.price):
                    continue
                line = TrendLine(kind, a.index, a.price, b.index, b.price, 0, n - 1, False, 0.0)
                span = np.arange(a.index, b.index + 1)
                values = np.array([line.value(i) for i in span])
                side = s.low[span] - values if kind == "up" else values - s.high[span]
                if np.any(side < -tolerance):
                    continue
                if abs(b.price - a.price) / (b.index - a.index) > 0.03 * a.price:
                    continue  # steeper than 3% a bar: a spike, not a trend
                after = np.arange(b.index + 1, n)
                closes = s.close[after]
                limits = np.array([line.value(i) for i in after])
                broken = np.flatnonzero(closes < limits - tolerance if kind == "up" else closes > limits + tolerance)
                if broken.size:
                    line.broken, line.end_index = True, int(after[broken[0]])
                line.touches = sum(1 for p in pivots if p.confirmed and p.kind == points[0].kind
                                   and a.index <= p.index <= line.end_index
                                   and abs(p.price - line.value(p.index)) <= tolerance)
                recency = 1 - (n - 1 - line.end_index) / n if line.broken else 1.0
                line.score = round(line.touches * 2 + (b.index - a.index) / n + (0 if line.broken else 2) + recency, 3)
                if best is None or line.score > best.score:
                    best = line
        if best is not None and (not best.broken or n - 1 - best.end_index <= 20):
            span = np.arange(best.i1, best.end_index + 1)
            values = np.array([best.value(i) for i in span])
            gaps = s.high[span] - values if kind == "up" else values - s.low[span]
            offset = float(gaps.max())
            best.channel_offset = (offset if kind == "up" else -offset) if offset > tolerance else None
            out.append(best)
    return out


# -- Fibonacci ---------------------------------------------------------------------------------------

FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def fibonacci_leg(pivots: list[Pivot]) -> dict[str, Any] | None:
    """The latest swing (the last two swing points; the last may be provisional)
    and its retracement levels."""
    if len(pivots) < 2:
        return None
    a, b = pivots[-2], pivots[-1]
    return {"from_index": a.index, "from_price": a.price, "to_index": b.index, "to_price": b.price,
            "confirmed": b.confirmed,
            "levels": [{"ratio": r, "price": b.price - (b.price - a.price) * r} for r in FIB_RATIOS]}


# -- chart patterns ------------------------------------------------------------------------------------


@dataclass
class Pattern:
    kind: str
    name: str
    direction: str  # bullish | bearish | neutral
    status: str  # forming | confirmed | failed
    points: list[tuple[int, float]]  # the defining swing points, in order
    lines: list[tuple[int, float, int, float]] = field(default_factory=list)  # necklines, boundaries
    start_index: int = 0
    end_index: int = 0  # the last defining point, or the breakout
    breakout_index: int | None = None
    target: float | None = None
    note: str = ""


def measured_move(start: float, height: float, up: bool) -> float:
    """A pattern's height projected from ``start``.  Downwards, a move deeper
    than 80% (big patterns after a run-up) is projected in proportion
    instead, so the target stays a price: start * start / (start + height)."""
    if up:
        return start + height
    target = start - height
    return target if target > 0.2 * start else start * start / (start + height)


def _near(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance


def _line_at(i1: int, p1: float, i2: int, p2: float, index: float) -> float:
    return p1 if i2 == i1 else p1 + (p2 - p1) / (i2 - i1) * (index - i1)


def _breakout(s: Series, start: int, level, below: bool, until: int | None = None) -> int | None:
    """First bar after ``start`` closing beyond ``level(index)``."""
    for k in range(start + 1, len(s) if until is None else min(until, len(s))):
        value = level(k)
        if (below and s.close[k] < value) or (not below and s.close[k] > value):
            return k
    return None


def _tolerance(price: float, atr_value: float) -> float:
    return max(0.015 * price, 0.5 * atr_value)


def _head_and_shoulders(s: Series, pts: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    out = []
    for k in range(len(pts) - 4):
        p = pts[k:k + 5]
        top = p[0].kind == "H"
        s1, t1, head, t2, s2 = p
        sign = 1 if top else -1
        tol = _tolerance(head.price, float(atr[head.index]))
        if not (sign * (head.price - s1.price) > tol and sign * (head.price - s2.price) > tol):
            continue
        if not (_near(s1.price, s2.price, 1.5 * tol) and _near(t1.price, t2.price, 2.5 * tol)):
            continue
        left, right = head.index - s1.index, s2.index - head.index
        if not (1 / 3 <= left / max(right, 1) <= 3):
            continue
        neck = lambda i, a=t1, b=t2: _line_at(a.index, a.price, b.index, b.price, i)  # noqa: E731
        failed_at = next((j for j in range(s2.index + 1, len(s))
                          if (top and s.high[j] > head.price) or (not top and s.low[j] < head.price)), None)
        brk = _breakout(s, s2.index, neck, below=top, until=failed_at)
        height = abs(head.price - neck(head.index))
        status = "confirmed" if brk is not None else "failed" if failed_at is not None else "forming"
        out.append(Pattern("head_shoulders_top" if top else "head_shoulders_bottom", "头肩顶" if top else "头肩底",
                           "bearish" if top else "bullish", status, [(q.index, q.price) for q in p],
                           [(t1.index, t1.price, brk if brk is not None else len(s) - 1,
                             neck(brk if brk is not None else len(s) - 1))],
                           s1.index, brk if brk is not None else s2.index, brk,
                           measured_move(neck(brk if brk is not None else len(s) - 1), height, up=not top),
                           "跌破颈线确认" if top else "突破颈线确认"))
    return out


def _multiple_tops(s: Series, pts: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    out = []
    for size in (5, 3):  # triple before double, so a triple top is not also reported as a double
        for k in range(len(pts) - size + 1):
            p = pts[k:k + size]
            top = p[0].kind == "H"
            peaks, troughs = p[0::2], p[1::2]
            level = float(np.mean([q.price for q in peaks]))
            tol = _tolerance(level, float(atr[p[-1].index]))
            if not all(_near(q.price, level, tol) for q in peaks):
                continue
            if len(troughs) > 1 and not _near(troughs[0].price, troughs[1].price, 2 * tol):
                continue  # rising (or falling) troughs: a triangle, not a triple top
            floor = min(q.price for q in troughs) if top else max(q.price for q in troughs)
            if abs(level - floor) < 3 * float(atr[p[-1].index]) or p[-1].index - p[0].index < 8:
                continue
            failed_at = next((j for j in range(p[-1].index + 1, len(s))
                              if (top and s.high[j] > level + tol) or (not top and s.low[j] < level - tol)), None)
            brk = _breakout(s, p[-1].index, lambda i, f=floor: f, below=top, until=failed_at)
            status = "confirmed" if brk is not None else "failed" if failed_at is not None else "forming"
            name = ("三重顶" if top else "三重底") if size == 5 else ("双顶（M 头）" if top else "双底（W 底）")
            kind = ("triple_top" if top else "triple_bottom") if size == 5 else ("double_top" if top else "double_bottom")
            end = brk if brk is not None else len(s) - 1
            out.append(Pattern(kind, name, "bearish" if top else "bullish", status, [(q.index, q.price) for q in p],
                               [(troughs[0].index, floor, end, floor)], p[0].index,
                               brk if brk is not None else p[-1].index, brk,
                               measured_move(floor, abs(level - floor), up=not top),
                               "跌破颈线确认" if top else "突破颈线确认"))
    return out


def _fit(points: list[Pivot]) -> tuple[float, float]:
    x = np.array([p.index for p in points], dtype=float)
    y = np.array([p.price for p in points])
    if len(points) == 1 or np.ptp(x) == 0:
        return 0.0, float(y.mean())
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


CONVERGING = {
    ("flat", "up"): ("ascending_triangle", "上升三角形", "bullish"),
    ("down", "flat"): ("descending_triangle", "下降三角形", "bearish"),
    ("down", "up"): ("symmetric_triangle", "对称三角形", "neutral"),
    ("up", "up"): ("rising_wedge", "上升楔形", "bearish"),
    ("down", "down"): ("falling_wedge", "下降楔形", "bullish"),
    ("flat", "flat"): ("rectangle", "矩形整理（箱体）", "neutral"),
}


def _consolidations(s: Series, pts: list[Pivot], atr: np.ndarray) -> list[Pattern]:
    """Triangles, wedges and rectangles on the last five or six swing points,
    or on those before the last one (which may be the breakout's own swing)."""
    out: list[Pattern] = []
    n = len(s)
    windows = [pts[end - size:end] for end in (len(pts), len(pts) - 1) for size in (6, 5) if end - size >= 0]
    for p in windows:
        highs = [q for q in p if q.kind == "H"]
        lows = [q for q in p if q.kind == "L"]
        span = p[-1].index - p[0].index
        if len(highs) < 2 or len(lows) < 2 or span < 10:
            continue
        (su, iu), (sl, il) = _fit(highs), _fit(lows)
        tol = _tolerance(float(np.mean([q.price for q in p])), float(atr[p[-1].index]))
        if any(abs(q.price - (su * q.index + iu)) > tol for q in highs) or \
           any(abs(q.price - (sl * q.index + il)) > tol for q in lows):
            continue  # the swing points do not line up

        def trend(slope: float) -> str:
            return "flat" if abs(slope * span) <= tol else "up" if slope > 0 else "down"

        shape = CONVERGING.get((trend(su), trend(sl)))
        if shape is None:
            continue
        kind, name, direction = shape
        first, last = p[0].index, p[-1].index
        width_start = (su * first + iu) - (sl * first + il)
        width_end = (su * last + iu) - (sl * last + il)
        if width_end <= 0 or width_start <= 0:
            continue
        if kind == "rectangle":
            if width_end < 2 * tol:
                continue
        elif width_end > 0.8 * width_start:
            continue  # not converging
        elif kind in ("rising_wedge", "falling_wedge") and not (
                (kind == "rising_wedge" and sl > su) or (kind == "falling_wedge" and su < sl)):
            continue
        upper = lambda i: su * i + iu  # noqa: E731
        lower = lambda i: sl * i + il  # noqa: E731
        up_break = _breakout(s, last, upper, below=False)
        down_break = _breakout(s, last, lower, below=True)
        brk = min((b for b in (up_break, down_break) if b is not None), default=None)
        if brk is None and kind != "rectangle" and su != sl:
            apex = (il - iu) / (su - sl)
            if apex < n - 1:
                continue  # the lines met without a breakout: stale
        end = brk if brk is not None else n - 1
        moved = "bullish" if brk is not None and brk == up_break else "bearish" if brk is not None else direction
        status = "confirmed" if brk is not None else "forming"
        if brk is not None and direction != "neutral" and moved != direction:
            status = "failed"  # broke the other way
        height = width_start
        target = (measured_move(upper(brk), height, up=True) if moved == "bullish"
                  else measured_move(lower(brk), height, up=False)) if brk is not None else None
        out.append(Pattern(kind, name, moved if brk is not None else direction, status,
                           [(q.index, q.price) for q in p],
                           [(first, upper(first), end, upper(end)), (first, lower(first), end, lower(end))],
                           first, end, brk, target, "向上突破" if moved == "bullish" and brk else
                           "向下突破" if brk else "等待突破"))
        break  # the widest view that forms a shape wins
    return out


def _flags(s: Series, atr: np.ndarray, pole_bars: int = 15, lookback: int = 60) -> list[Pattern]:
    """A sharp pole (at least 4 ATR and 10% within 15 bars) and a small
    consolidation after it (4 to 25 bars, retracing at most half the pole,
    staying inside it), found on the bars themselves: the consolidation is
    smaller than a swing, so the pole's end is usually no swing point."""
    out: list[Pattern] = []
    n = len(s)
    for bull in (True, False):
        for b in range(n - 5, max(pole_bars, n - lookback) - 1, -1):  # the latest pole first
            window = slice(b - pole_bars, b)
            if bull:
                if s.high[b] < s.high[window].max() or s.high[b] <= s.high[b - pole_bars:b + 1][:-1].max(initial=0):
                    continue
                a = b - pole_bars + int(np.argmin(s.low[window]))
                pole = s.high[b] - s.low[a]
                base = s.low[a]
            else:
                if s.low[b] > s.low[window].min():
                    continue
                a = b - pole_bars + int(np.argmax(s.high[window]))
                pole = s.high[a] - s.low[b]
                base = s.high[a]
            if pole < max(4 * float(atr[b]), 0.10 * base):
                continue
            tip = s.high[b] if bull else s.low[b]
            brk = next((j for j in range(b + 1, n) if (bull and s.close[j] > tip) or (not bull and s.close[j] < tip)),
                       None)
            body = np.arange(b + 1, brk if brk is not None else n)
            if not 4 <= body.size <= 25:
                continue
            poke = 0.5 * float(atr[b])  # an intraday poke beyond the tip, without a close there, is allowed
            if bull and s.high[body].max() > tip + poke or not bull and s.low[body].min() < tip - poke:
                continue
            pullback = (tip - s.low[body].min()) if bull else (s.high[body].max() - tip)
            if pullback <= 0 or pullback > 0.5 * pole:
                continue
            su, iu = np.polyfit(body, s.high[body], 1)
            sl, il = np.polyfit(body, s.low[body], 1)
            first, last = int(body[0]), int(body[-1])
            converging = (su * last + iu - sl * last - il) < 0.6 * (su * first + iu - sl * first - il)
            end = brk if brk is not None else n - 1
            out.append(Pattern("pennant" if converging else "flag", "三角旗形" if converging else "旗形",
                               "bullish" if bull else "bearish", "confirmed" if brk is not None else "forming",
                               [(a, float(base)), (b, float(tip))],
                               [(first, float(su * first + iu), end, float(su * end + iu)),
                                (first, float(sl * first + il), end, float(sl * end + il))],
                               a, end, brk, float(measured_move(tip, pole, up=bull)),
                               "旗杆后的小幅整理，突破旗杆顶点确认" if bull else "急跌后的小幅整理，跌破旗杆底点确认"))
            break  # the latest pole in this direction
    return out


PRIORITY = {"head_shoulders_top": 0, "head_shoulders_bottom": 0, "triple_top": 1, "triple_bottom": 1,
            "double_top": 2, "double_bottom": 2}


def chart_patterns(s: Series, pivots: list[Pivot], atr: np.ndarray, recent: int = RECENT_BARS) -> list[Pattern]:
    """Recent patterns, the most specific kept where two overlap."""
    n = len(s)
    found = (_head_and_shoulders(s, pivots, atr) + _multiple_tops(s, pivots, atr)
             + _consolidations(s, pivots, atr) + _flags(s, atr))
    found = [p for p in found if p.end_index >= n - recent]
    found.sort(key=lambda p: (p.status == "failed", PRIORITY.get(p.kind, 3), -p.end_index))
    kept: list[Pattern] = []
    for pattern in found:
        span = set(range(pattern.start_index, pattern.end_index + 1))
        if any(len(span & set(range(k.start_index, k.end_index + 1))) > 0.5 * len(span) for k in kept):
            continue
        kept.append(pattern)
    return sorted(kept, key=lambda p: p.start_index)


# -- candlestick patterns ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candle:
    index: int
    kind: str
    name: str
    label: str  # one character for the chart badge
    direction: str  # bullish | bearish | neutral
    bars: int  # how many bars make it


def candle_patterns(s: Series, recent: int = 250) -> list[Candle]:
    """Classic one-, two- and three-bar patterns in their usual trend context
    (a five-bar move of at least one average range into them), on the last
    ``recent`` bars; where several fit a bar, the longest wins.  One-price
    bars (limit up or down all day) are skipped."""
    n = len(s)
    o, h, lo, c = s.open, s.high, s.low, s.close
    body = np.abs(c - o)
    rng = h - lo
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - lo
    avg_body = np.array([body[max(0, i - 10):i].mean() if i else body[0] for i in range(n)])
    avg_range = np.array([rng[max(0, i - 10):i].mean() if i else rng[0] for i in range(n)])

    def falling(i: int) -> bool:  # a real move into bar i, not drift
        return i >= 6 and c[i - 6] - c[i - 1] >= 1.0 * avg_range[i]

    def rising(i: int) -> bool:
        return i >= 6 and c[i - 1] - c[i - 6] >= 1.0 * avg_range[i]

    def bull(i: int) -> bool:
        return c[i] > o[i]

    def bear(i: int) -> bool:
        return c[i] < o[i]

    def long_body(i: int) -> bool:
        return body[i] >= max(avg_body[i], 0.5 * rng[i]) and rng[i] > 0

    out: list[Candle] = []
    for i in range(max(1, n - recent), n):
        if rng[i] <= 0 or avg_range[i] <= 0:
            continue
        hit: Candle | None = None
        # three bars
        if i >= 2 and rng[i - 1] > 0 and rng[i - 2] > 0:
            b1, b2 = i - 2, i - 1
            if falling(b1) and bear(b1) and long_body(b1) and body[b2] <= 0.3 * body[b1] \
                    and max(o[b2], c[b2]) <= c[b1] + 0.1 * body[b1] and bull(i) and c[i] >= (o[b1] + c[b1]) / 2:
                hit = Candle(i, "morning_star", "早晨之星", "晨", "bullish", 3)
            elif rising(b1) and bull(b1) and long_body(b1) and body[b2] <= 0.3 * body[b1] \
                    and min(o[b2], c[b2]) >= c[b1] - 0.1 * body[b1] and bear(i) and c[i] <= (o[b1] + c[b1]) / 2:
                hit = Candle(i, "evening_star", "黄昏之星", "昏", "bearish", 3)
            elif falling(b1) and all(bull(j) and body[j] >= 0.6 * avg_body[j] and upper[j] <= 0.3 * body[j]
                                     for j in (b1, b2, i)) \
                    and c[b1] < c[b2] < c[i] and o[b1] < o[b2] <= c[b1] and o[b2] < o[i] <= c[b2]:
                hit = Candle(i, "three_white_soldiers", "红三兵", "兵", "bullish", 3)
            elif rising(b1) and all(bear(j) and body[j] >= 0.6 * avg_body[j] and lower[j] <= 0.3 * body[j]
                                    for j in (b1, b2, i)) \
                    and c[b1] > c[b2] > c[i] and o[b1] > o[b2] >= c[b1] and o[b2] > o[i] >= c[b2]:
                hit = Candle(i, "three_black_crows", "三只乌鸦", "鸦", "bearish", 3)
        # two bars
        p = i - 1
        if hit is None and rng[p] > 0:
            engulfing = body[i] > body[p] and body[i] >= avg_body[i] and body[p] >= 0.3 * avg_body[i]
            if falling(i) and engulfing and bear(p) and bull(i) and o[i] <= c[p] and c[i] >= o[p]:
                hit = Candle(i, "bullish_engulfing", "看涨吞没", "吞", "bullish", 2)
            elif rising(i) and engulfing and bull(p) and bear(i) and o[i] >= c[p] and c[i] <= o[p]:
                hit = Candle(i, "bearish_engulfing", "看跌吞没", "吞", "bearish", 2)
            elif falling(i) and bear(p) and long_body(p) and bull(i) and o[i] < c[p] \
                    and (o[p] + c[p]) / 2 <= c[i] < o[p]:
                hit = Candle(i, "piercing", "刺透形态", "刺", "bullish", 2)
            elif rising(i) and bull(p) and long_body(p) and bear(i) and o[i] > c[p] \
                    and o[p] < c[i] <= (o[p] + c[p]) / 2:
                hit = Candle(i, "dark_cloud", "乌云盖顶", "乌", "bearish", 2)
        # one bar (only bars of a normal size, so dull days are not marked)
        if hit is None and rng[i] >= 0.8 * avg_range[i]:
            small_top, small_bottom = upper[i] <= 0.1 * rng[i], lower[i] <= 0.1 * rng[i]
            if body[i] <= 0.05 * rng[i] and rng[i] >= avg_range[i] and (falling(i) or rising(i)):
                hit = Candle(i, "doji", "十字星", "十", "neutral", 1)
            elif lower[i] >= 2 * body[i] and small_top and body[i] > 0:
                if falling(i):
                    hit = Candle(i, "hammer", "锤子线", "锤", "bullish", 1)
                elif rising(i):
                    hit = Candle(i, "hanging_man", "上吊线", "吊", "bearish", 1)
            elif upper[i] >= 2 * body[i] and small_bottom and body[i] > 0:
                if falling(i):
                    hit = Candle(i, "inverted_hammer", "倒锤子", "倒", "bullish", 1)
                elif rising(i):
                    hit = Candle(i, "shooting_star", "射击之星", "射", "bearish", 1)
        if hit is not None:
            out.append(hit)
    return out


# -- everything together -----------------------------------------------------------------------------


def analyse(bars: list[dict[str, Any]], sensitivity: str = "medium") -> dict[str, Any]:
    """All automatic lines for the chart, with dates instead of bar indices."""
    s = Series.from_bars(bars)
    if len(s) < 20:
        return {"bars": len(s), "pivots": [], "levels": [], "trendlines": [], "fibonacci": None, "patterns": [],
                "candles": []}
    atr = average_true_range(s)
    pivots = zigzag(s, SENSITIVITY.get(sensitivity, SENSITIVITY["medium"]), atr)
    d = s.dates

    def point(index: int, price: float) -> dict[str, Any]:
        return {"date": d[index], "price": round(price, 4)}

    levels = []
    for lv in support_resistance(s, pivots, atr):
        levels.append({**{k: v for k, v in asdict(lv).items() if k not in ("first_index", "last_index")},
                       "first_date": d[lv.first_index], "last_date": d[lv.last_index]})
    lines = []
    for tl in trend_lines(s, pivots, atr):
        end_value = tl.value(tl.end_index)
        lines.append({"kind": tl.kind, "start": point(tl.i1, tl.p1), "anchor": point(tl.i2, tl.p2),
                      "end": point(tl.end_index, end_value), "touches": tl.touches, "broken": tl.broken,
                      "broken_date": d[tl.end_index] if tl.broken else None, "score": tl.score,
                      "channel": None if tl.channel_offset is None else {
                          "start": point(tl.i1, tl.p1 + tl.channel_offset),
                          "end": point(tl.end_index, end_value + tl.channel_offset)}})
    fib = fibonacci_leg(pivots)
    patterns = []
    for p in chart_patterns(s, pivots, atr):
        patterns.append({"kind": p.kind, "name": p.name, "direction": p.direction, "status": p.status,
                         "points": [point(i, v) for i, v in p.points],
                         "lines": [{"start": point(a, pa), "end": point(b, pb)} for a, pa, b, pb in p.lines],
                         "start_date": d[p.start_index], "end_date": d[p.end_index],
                         "breakout_date": d[p.breakout_index] if p.breakout_index is not None else None,
                         "target": None if p.target is None else round(p.target, 4), "note": p.note})
    return {
        "bars": len(s), "first_date": d[0], "last_date": d[-1], "atr": round(float(atr[-1]), 4),
        "sensitivity": sensitivity,
        "pivots": [{**point(p.index, p.price), "kind": p.kind, "confirmed": p.confirmed} for p in pivots],
        "levels": levels, "trendlines": lines,
        "fibonacci": None if fib is None else {
            "from": point(fib["from_index"], fib["from_price"]), "to": point(fib["to_index"], fib["to_price"]),
            "confirmed": fib["confirmed"], "levels": [{"ratio": x["ratio"], "price": round(x["price"], 4)}
                                                      for x in fib["levels"]]},
        "patterns": patterns,
        "candles": [{"date": d[c.index], "kind": c.kind, "name": c.name, "label": c.label,
                     "direction": c.direction, "bars": c.bars} for c in candle_patterns(s)],
    }
