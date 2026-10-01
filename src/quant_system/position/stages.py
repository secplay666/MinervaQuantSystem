"""Weinstein four-stage view of a price series (design: docs/design/position-manager.md §3-4).

Stages: 1 base (筑底), 2 advance (右侧), 3 top (顶部), 4 decline (左侧); 0 while the
moving average does not exist yet.

The long moving average's direction over ``slope`` sessions and the price's
place against it decide the stage:

* MA rising, price not below the band -> 2;  MA falling, price not above -> 4;
* MA flat -> 3 after an advance, 1 after a decline;
* a sharp break below a still rising MA -> 3; a sharp rally above a still
  falling one -> 1;
* when history starts flat, a rise of ``rise`` over the ``context`` low says
  top, otherwise base.

A new stage is adopted only after it held for ``confirm`` sessions.  Indices
move less, so they use their own flat threshold.  All of this is a view to
help the user; the direction label the rules act on is the user's.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

import numpy as np
import pandas as pd

UNKNOWN, BASE, ADVANCE, TOP, DECLINE = 0, 1, 2, 3, 4
STAGE_KEYS = {UNKNOWN: "unknown", BASE: "base", ADVANCE: "advance", TOP: "top", DECLINE: "decline"}
STAGE_NAMES = {UNKNOWN: "数据不足", BASE: "筑底", ADVANCE: "右侧", TOP: "顶部", DECLINE: "左侧"}


@dataclass(frozen=True)
class StageParams:
    ma: int = 200             # moving average (sessions)
    slope: int = 20           # MA_t / MA_{t-slope} - 1
    flat: float = 0.015       # |slope| below this: flat (stocks)
    index_flat: float = 0.006  # the same for indices
    band: float = 0.03        # within MA x (1 +- band) the price is "at" the MA
    confirm: int = 10         # sessions a new stage must hold
    context: int = 250        # lookback when history starts with a flat MA
    rise: float = 0.5         # risen this much from the context low: top rather than base
    # Base criteria (筑底判据 ② and ③)
    dry_window: int = 10      # recent volume window
    dry_compare: int = 20     # the window before it
    dry_ratio: float = 0.7    # recent mean below this share of the one before: dried up
    surge_multiple: float = 2.0  # an up day with volume >= this x the recent mean
    rebound: float = 0.05     # a retest needs a rebound of this much from the low first

    def for_index(self) -> StageParams:
        return replace(self, flat=self.index_flat)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict | None, base: StageParams | None = None) -> StageParams:
        start = base or cls()
        names = {f.name for f in fields(cls)}
        clean = {k: type(getattr(start, k))(v) for k, v in (values or {}).items() if k in names and v is not None}
        params = replace(start, **clean)
        params.validate()
        return params

    def validate(self) -> None:
        if not (20 <= self.ma <= 400 and 2 <= self.slope <= 120 and 1 <= self.confirm <= 60):
            raise ValueError("均线周期 20-400、斜率回看 2-120、确认天数 1-60")
        if not (0 < self.flat < 0.2 and 0 < self.index_flat < 0.2 and 0 <= self.band < 0.3):
            raise ValueError("走平阈值须在 0-20% 之间，均线附近区间须在 0-30% 之间")
        if not (self.context >= 20 and self.rise > 0 and self.dry_window >= 2 and self.dry_compare >= 2
                and 0 < self.dry_ratio < 1 and self.surge_multiple > 1 and 0 < self.rebound < 1):
            raise ValueError("筑底判据参数不合法")


PRESETS: dict[str, tuple[str, StageParams]] = {
    "steady": ("稳健（200 日）", StageParams()),
    "classic": ("经典（150 日，Weinstein 30 周）", StageParams(ma=150, flat=0.02, confirm=5)),
    "short": ("短线（60 日）", StageParams(ma=60, slope=10, flat=0.02, confirm=3)),
}


def classify(price: np.ndarray, p: StageParams) -> np.ndarray:
    """[T, N] (or [T]) adjusted closes, NaN where missing -> stage codes of the same shape."""
    single = price.ndim == 1
    values = price[:, None] if single else price
    frame = pd.DataFrame(values)
    ma = frame.rolling(p.ma, min_periods=p.ma).mean().to_numpy()
    slope = np.full_like(ma, np.nan)
    slope[p.slope:] = ma[p.slope:] / ma[:-p.slope] - 1
    low = frame.rolling(p.context, min_periods=20).min().to_numpy()
    if single:  # one series (the board, the chart): a scalar loop, the per-step numpy overhead dominates
        return _classify_one(values[:, 0].tolist(), ma[:, 0].tolist(), slope[:, 0].tolist(), low[:, 0].tolist(), p)
    T, N = values.shape
    stage = np.zeros((T, N), dtype=np.int8)
    state = np.zeros(N, dtype=np.int8)
    pending = np.zeros(N, dtype=np.int8)
    count = np.zeros(N, dtype=np.int32)
    for t in range(T):
        s, c, m = slope[t], values[t], ma[t]
        valid = np.isfinite(s) & np.isfinite(c) & np.isfinite(m)
        with np.errstate(invalid="ignore"):
            up, down = s > p.flat, s < -p.flat
            above_band, below_band = c > m * (1 + p.band), c < m * (1 - p.band)
            first_top = c / low[t] - 1 > p.rise
        flat = ~up & ~down
        was_up = (state == ADVANCE) | (state == TOP)
        was_down = (state == DECLINE) | (state == BASE)
        unknown = state == UNKNOWN
        cand = state.copy()
        cand[up & ~below_band] = ADVANCE
        cand[down & ~above_band] = DECLINE
        cand[flat & was_up] = TOP
        cand[flat & was_down] = BASE
        start = flat & unknown
        cand[start] = np.where(first_top[start], TOP, BASE)
        cand[up & below_band & was_up] = TOP
        cand[down & above_band & was_down] = BASE
        cand[~valid] = state[~valid]
        count = np.where(cand == pending, count + 1, 1)
        pending = cand
        switch = valid & (cand != state) & ((count >= p.confirm) | unknown)
        state = np.where(switch, cand, state).astype(np.int8)
        stage[t] = state
    return stage[:, 0] if single else stage


def _classify_one(close: list[float], ma: list[float], slope: list[float], low: list[float], p: StageParams) -> np.ndarray:
    """``classify`` for one series, step for step the same rules (tests compare the two)."""
    stage = np.zeros(len(close), dtype=np.int8)
    state, pending, count = UNKNOWN, UNKNOWN, 0
    for t, (c, m, s, lo) in enumerate(zip(close, ma, slope, low)):
        valid = math.isfinite(s) and math.isfinite(c) and math.isfinite(m)
        up, down = s > p.flat, s < -p.flat  # False for NaN, as in numpy
        above_band, below_band = c > m * (1 + p.band), c < m * (1 - p.band)
        # numpy: x / 0 is inf for x > 0, NaN comparisons are False
        first_top = False if lo != lo else (c > 0 if lo == 0 else c / lo - 1 > p.rise)
        flat = not up and not down
        was_up = state in (ADVANCE, TOP)
        was_down = state in (DECLINE, BASE)
        unknown = state == UNKNOWN
        cand = state
        if up and not below_band:
            cand = ADVANCE
        if down and not above_band:
            cand = DECLINE
        if flat and was_up:
            cand = TOP
        if flat and was_down:
            cand = BASE
        if flat and unknown:
            cand = TOP if first_top else BASE
        if up and below_band and was_up:
            cand = TOP
        if down and above_band and was_down:
            cand = BASE
        if not valid:
            cand = state
        count = count + 1 if cand == pending else 1
        pending = cand
        if valid and cand != state and (count >= p.confirm or unknown):
            state = cand
        stage[t] = state
    return stage


def volume_surge(close: np.ndarray, open_: np.ndarray, volume: np.ndarray, p: StageParams) -> np.ndarray:
    """Criterion ②: after volume dried up, an up day with a volume surge (the
    recent mean excludes the day itself, so the surge does not raise its own bar)."""
    v = pd.Series(volume, dtype="float64")
    recent = v.shift(1).rolling(p.dry_window, min_periods=max(2, p.dry_window - 2)).mean()
    before = v.shift(1 + p.dry_window).rolling(p.dry_compare, min_periods=max(2, p.dry_compare - 5)).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        dried = (recent / before < p.dry_ratio).to_numpy()
        surge = (v >= p.surge_multiple * recent).to_numpy() & (close > open_)
    return dried & surge


def retest_holds(close: np.ndarray, since: int, t: int, p: StageParams) -> bool:
    """Criterion ③ on day ``t``: the lowest close since ``since`` (entering the
    base) was followed by a rebound of ``rebound``, and ``t`` is back near
    that low without closing below it."""
    window = close[since:t + 1]
    if len(window) < 5 or not np.isfinite(window).any():
        return False
    k = int(np.nanargmin(window[:-1])) if len(window) > 1 else 0
    low = window[k]
    after = window[k + 1:-1]
    if not len(after) or not np.isfinite(low):
        return False
    rebounded = np.nanmax(after) >= low * (1 + p.rebound)
    today = window[-1]
    return bool(rebounded and low <= today <= low * (1 + p.rebound / 2))


def stage_view(price: np.ndarray, p: StageParams, is_index: bool = False) -> dict:
    """The latest stage of one adjusted-close series, with the numbers behind it."""
    params = p.for_index() if is_index else p
    stages = classify(price, params)
    last = int(stages[-1]) if len(stages) else UNKNOWN
    if last == UNKNOWN:
        return {"stage": STAGE_KEYS[UNKNOWN], "name": STAGE_NAMES[UNKNOWN],
                "reason": f"历史不足 {params.ma + params.slope} 个交易日", "since_index": None, "days": 0}
    changes = np.flatnonzero(stages != last)
    since = int(changes[-1]) + 1 if len(changes) else 0
    ma = pd.Series(price).rolling(params.ma, min_periods=params.ma).mean().to_numpy()
    slope = ma[-1] / ma[-1 - params.slope] - 1
    gap = price[-1] / ma[-1] - 1
    trend = "向上" if slope > params.flat else "向下" if slope < -params.flat else "走平"
    previous = int(stages[since - 1]) if since > 0 else UNKNOWN
    reason = (f"{params.ma} 日均线 {params.slope} 日内 {slope:+.1%}（{trend}，阈值 ±{params.flat:.1%}），"
              f"收盘{'高于' if gap >= 0 else '低于'}均线 {abs(gap):.1%}；"
              f"已持续 {len(stages) - since} 个交易日"
              + (f"，之前为{STAGE_NAMES[previous]}" if previous != UNKNOWN else ""))
    return {"stage": STAGE_KEYS[last], "name": STAGE_NAMES[last], "reason": reason, "since_index": since,
            "days": len(stages) - since, "ma": float(ma[-1]), "slope": float(slope), "gap": float(gap),
            "previous": STAGE_KEYS[previous]}
