"""Money map (docs/design/money-map.md): how crowded each SW L1 industry's
trading is against its own past, next to its 12-month return, week by week,
and what followed the same state before.

Crowding C = the industry index's share of the industries' turnover (20-session
mean) / the same share's mean over 750 sessions.  Above 1 the industry trades
more than it used to.  It measures where trading concentrates, not money
flowing in (every trade has a buyer and a seller).

History statistics are point in time: at week t they use only the samples
whose forward window had ended by t.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

SHORT = 20            # sessions in the recent turnover share
LONG = 750            # sessions in the baseline (about three years)
LONG_MIN = 675        # a C counts in the statistics once its baseline has this many sessions
BASE_MIN = 250        # C is shown from this many sessions on, flagged until LONG_MIN
R3, R12, CHANGE = 60, 250, 60
CROWDED = 1.8         # 必须减
FIGHT = (0.8, 1.4)    # 钱在这里打架: C in this range with a 12-month return of at least FIGHT_RETURN
FIGHT_RETURN = 0.30
STARTING = (0.6, 0.8)  # 刚开始动: C in this range with a 12-month return in STARTING_RETURN
STARTING_RETURN = (0.15, 0.50)
QUIET_RETURN = 0.15   # 没到时间: C below 0.8 and a 12-month return below this
HORIZONS = (13, 26)   # weeks ahead in the statistics
ENTRY_GAP = 13        # weeks between two entries above CROWDED of one industry

ZONES = {"crowded": "必须减", "fight": "钱在这里打架", "starting": "刚开始动", "waiting": "没到时间", "other": "其他"}
C_BANDS = ((-math.inf, 0.8, "C<0.8"), (0.8, 1.4, "C 0.8–1.4"), (1.4, 1.8, "C 1.4–1.8"), (1.8, math.inf, "C>1.8"))
R_BANDS = ((-math.inf, 0.15, "<15%"), (0.15, 0.30, "15–30%"), (0.30, 0.50, "30–50%"), (0.50, math.inf, ">50%"))
CELLS = [f"{c}|{r}" for _, _, c in C_BANDS for _, _, r in R_BANDS]


def daily_measures(close: pd.DataFrame, amount: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Session x industry frames: share, C, its baseline length, ΔC (60 sessions), 3- and 12-month returns."""
    share = amount.div(amount.sum(axis=1), axis=0)
    base = share.rolling(LONG, min_periods=1).count()
    c = share.rolling(SHORT, min_periods=SHORT).mean() / share.rolling(LONG, min_periods=BASE_MIN).mean()
    return {"share": share, "c": c, "base": base, "dc60": c - c.shift(CHANGE),
            "r3": close / close.shift(R3) - 1, "r12": close / close.shift(R12) - 1}


def zone_of(c: np.ndarray, r12: np.ndarray) -> np.ndarray:
    c, r12 = np.asarray(c, dtype=float), np.asarray(r12, dtype=float)
    conditions = [
        c > CROWDED,
        (c >= FIGHT[0]) & (c <= FIGHT[1]) & (r12 >= FIGHT_RETURN),
        (c >= STARTING[0]) & (c < STARTING[1]) & (r12 >= STARTING_RETURN[0]) & (r12 <= STARTING_RETURN[1]),
        (c < FIGHT[0]) & (r12 < QUIET_RETURN),
    ]
    out = np.select(conditions, ["crowded", "fight", "starting", "waiting"], default="other").astype(object)
    out[np.isnan(c) | np.isnan(r12)] = None
    return out


def cell_of(c: np.ndarray, r12: np.ndarray) -> np.ndarray:
    c, r12 = np.asarray(c, dtype=float), np.asarray(r12, dtype=float)
    c_label = np.select([(c > lo) & (c <= hi) for lo, hi, _ in C_BANDS], [label for _, _, label in C_BANDS], "")
    r_label = np.select([(r12 > lo) & (r12 <= hi) for lo, hi, _ in R_BANDS], [label for _, _, label in R_BANDS], "")
    out = np.char.add(np.char.add(c_label.astype(str), "|"), r_label.astype(str)).astype(object)
    out[np.isnan(c) | np.isnan(r12)] = None
    return out


def week_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The last session of each week (the latest week may still be running)."""
    ends = pd.Series(index, index=index).groupby(index.to_period("W-FRI")).max()
    return pd.DatetimeIndex(ends.to_numpy())


def weekly_table(close: pd.DataFrame, amount: pd.DataFrame, benchmark: pd.Series) -> pd.DataFrame:
    """One row per (week, industry) with the measures, zone, cell and the
    forward excess returns: ``x13``/``x26`` over the average industry,
    ``h13``/``h26`` over the benchmark (NaN until the window has passed)."""
    daily = daily_measures(close, amount)
    weeks = week_ends(close.index)
    level = close.loc[weeks]
    bench = benchmark.reindex(close.index).ffill().loc[weeks]
    parts = {name: frame.loc[weeks] for name, frame in daily.items()}
    for h in HORIZONS:
        forward = level.shift(-h) / level - 1
        parts[f"x{h}"] = forward.sub(forward.mean(axis=1), axis=0)
        parts[f"h{h}"] = forward.sub(bench.shift(-h) / bench - 1, axis=0)
    table = pd.concat({name: frame.stack(future_stack=True) for name, frame in parts.items()}, axis=1)
    table.index.names = ["week", "code"]
    table = table.reset_index()
    table["week_no"] = table["week"].map({week: i for i, week in enumerate(weeks)})
    table["zone"] = zone_of(table["c"], table["r12"])
    table["cell"] = cell_of(table["c"], table["r12"])
    table["full_base"] = table["base"] >= LONG_MIN
    return table[table["c"].notna()].reset_index(drop=True)


def entries(table: pd.DataFrame) -> pd.DataFrame:
    """First weeks above CROWDED, at least ENTRY_GAP weeks after the industry's previous week above."""
    above = table[(table["c"] > CROWDED)].sort_values(["code", "week_no"])
    gap = above.groupby("code")["week_no"].diff()
    return above[gap.isna() | (gap > ENTRY_GAP)].reset_index(drop=True)


def pit_stats(samples: pd.DataFrame, key: str, weeks: int) -> dict[int, dict[str, pd.DataFrame]]:
    """Cumulative sums by the week a sample's forward window ends: for each
    horizon, frames (week_no x key) of n, sum and losses of x and h."""
    out: dict[int, dict[str, pd.DataFrame]] = {}
    keys = sorted(samples[key].dropna().unique())
    for h in HORIZONS:
        valid = samples[samples[f"x{h}"].notna()]
        frame = pd.DataFrame({
            "known": valid["week_no"] + h, "key": valid[key],
            "n": 1.0, "x": valid[f"x{h}"], "x_lose": (valid[f"x{h}"] < 0).astype(float),
            "hn": valid[f"h{h}"].notna().astype(float), "h": valid[f"h{h}"].fillna(0.0),
            "h_lose": (valid[f"h{h}"] < 0).astype(float),
        })
        sums = frame.groupby(["known", "key"]).sum()
        out[h] = {stat: sums[stat].unstack("key").reindex(index=range(weeks), columns=keys).fillna(0.0).cumsum()
                  for stat in ("n", "x", "x_lose", "hn", "h", "h_lose")}
    return out


def stats_at(stats: dict[int, dict[str, pd.DataFrame]], week_no: int, key: Any) -> dict[str, Any]:
    """The statistics known at ``week_no`` for one key: per horizon n, mean
    excess and share of losses against the average industry (x) and the benchmark (h)."""
    out: dict[str, Any] = {}
    for h, frames in stats.items():
        if key not in frames["n"].columns:
            out[str(h)] = {"n": 0}
            continue
        n, hn = frames["n"].at[week_no, key], frames["hn"].at[week_no, key]
        out[str(h)] = {
            "n": int(n),
            "x": frames["x"].at[week_no, key] / n if n else None,
            "x_lose": frames["x_lose"].at[week_no, key] / n if n else None,
            "h": frames["h"].at[week_no, key] / hn if hn else None,
            "h_lose": frames["h_lose"].at[week_no, key] / hn if hn else None,
        }
    return out


def crossings(dates: list, c: np.ndarray, high: float = CROWDED, low: float = FIGHT[1]) -> list[tuple[Any, str, float]]:
    """(session, "crowded" | "relief", C) events of one industry's daily C: C
    rising above ``high``; then, after such a rise, C falling back below ``low``."""
    events, crowded = [], False
    previous = math.nan
    for day, value in zip(dates, c):
        if math.isnan(value):
            previous = value
            continue
        if value > high and not (previous > high):
            events.append((day, "crowded", float(value)))
            crowded = True
        elif crowded and value < low and not (previous < low):
            events.append((day, "relief", float(value)))
            crowded = False
        previous = value
    return events
