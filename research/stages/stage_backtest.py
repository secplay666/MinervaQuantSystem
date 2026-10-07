"""Weinstein four-stage classification on A-share daily bars, and how the stages performed.

Research prototype (read-only on the local catalog): stage per stock per day,
forward excess returns per stage and after each transition, a long-only
"hold stage 2" portfolio with and without the index gate, split by the CSI 300's
own stage (the market regime).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_system.backtest.market_data import load_market_data

ROOT = Path(__file__).resolve().parents[1] / "L1"
UNKNOWN, BASE, ADVANCE, TOP, DECLINE = 0, 1, 2, 3, 4
NAMES = {BASE: "1 筑底", ADVANCE: "2 右侧", TOP: "3 顶部", DECLINE: "4 左侧"}


@dataclass(frozen=True)
class StageParams:
    name: str
    ma: int = 150          # moving-average window (sessions); 150 ~ 30 weeks
    slope: int = 20        # slope lookback: MA_t / MA_{t-slope} - 1
    flat: float = 0.02     # |slope| below this = flat MA
    band: float = 0.03     # price within MA x (1 +- band) still counts as "at" the MA
    confirm: int = 5       # sessions a new stage must hold before it is adopted
    context: int = 250     # lookback for the first stage when history starts flat
    rise: float = 0.5      # ... risen this much from the low = top rather than base


def classify(price: np.ndarray, p: StageParams) -> np.ndarray:
    """[T, N] adjusted prices -> [T, N] stage codes (0 until the MA exists)."""
    frame = pd.DataFrame(price)
    ma = frame.rolling(p.ma, min_periods=p.ma).mean().to_numpy()
    slope = ma / np.roll(ma, p.slope, axis=0) - 1
    slope[: p.slope] = np.nan
    low = frame.rolling(p.context, min_periods=20).min().to_numpy()
    T, N = price.shape
    stage = np.zeros((T, N), dtype=np.int8)
    state = np.zeros(N, dtype=np.int8)
    pending = np.zeros(N, dtype=np.int8)
    count = np.zeros(N, dtype=np.int16)
    for t in range(T):
        s, c, m = slope[t], price[t], ma[t]
        valid = np.isfinite(s) & np.isfinite(c) & np.isfinite(m)
        up, down = s > p.flat, s < -p.flat
        flat = ~up & ~down
        above_band, below_band = c > m * (1 + p.band), c < m * (1 - p.band)
        was_up = (state == ADVANCE) | (state == TOP)
        was_down = (state == DECLINE) | (state == BASE)
        unknown = state == UNKNOWN
        first_top = unknown & (c / low[t] - 1 > p.rise)
        cand = state.copy()
        cand[up & ~below_band] = ADVANCE
        cand[down & ~above_band] = DECLINE
        cand[flat & was_up] = TOP
        cand[flat & was_down] = BASE
        cand[flat & unknown] = np.where(first_top[flat & unknown], TOP, BASE)
        cand[up & below_band & was_up] = TOP        # a sharp break below a still rising MA
        cand[down & above_band & was_down] = BASE   # a sharp rally above a still falling MA
        cand[~valid] = state[~valid]
        # Hysteresis: a new stage is adopted after holding ``confirm`` sessions.
        same = cand == pending
        count = np.where(same, count + 1, 1).astype(np.int16)
        pending = cand
        switch = (cand != state) & ((count >= p.confirm) | unknown) & valid
        state = np.where(switch, cand, state).astype(np.int8)
        stage[t] = state
    return stage


def forward_excess(price: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray]:
    fwd = np.full_like(price, np.nan)
    fwd[:-h] = price[h:] / price[:-h] - 1
    market = np.nanmean(fwd, axis=1, keepdims=True)
    return fwd, fwd - market


def main() -> None:
    market = load_market_data("duckdb", ROOT / "data" / "market.duckdb")
    sessions = pd.to_datetime(pd.Series(market.sessions))
    close = np.where(market.has_bar, market.close / 100.0, np.nan)
    price = pd.DataFrame(close * market.hfq).ffill().to_numpy()  # hfq-adjusted, carried over suspensions
    listed = market.bar_count >= 250  # a year of history before counting a stock
    with duckdb.connect(str(ROOT / "data" / "market.duckdb"), read_only=True) as con:
        idx = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY 1").fetchdf()
    idx = idx.set_index(pd.to_datetime(idx["trade_date"]))["close"].reindex(sessions).ffill().to_numpy()[:, None]

    fwd = {h: forward_excess(price, h) for h in (20, 60, 120)}
    daily = np.full_like(price, np.nan)
    daily[1:] = price[1:] / price[:-1] - 1
    market_daily = np.nanmean(np.where(listed, daily, np.nan), axis=1)
    start = int(np.searchsorted(sessions.to_numpy(), np.datetime64("2020-10-01")))

    sets = [StageParams("A 经典(150日,斜率20,平2%,确认5)"),
            StageParams("B 偏快(120日,确认3)", ma=120, confirm=3),
            StageParams("C 偏慢(200日,平1.5%,确认10)", ma=200, flat=0.015, confirm=10),
            StageParams("D 短线(60日,斜率10,平2%,确认3)", ma=60, slope=10, confirm=3)]
    only = [s for s in sets if s.name[0] in (sys.argv[1] if len(sys.argv) > 1 else "ABCD")]
    for p in only:
        stage = classify(price, p)
        index_stage = classify(idx, p)[:, 0]
        mask = listed.copy()
        mask[:start] = False
        print(f"\n==================== {p.name} ====================")
        print("阶段        占比    20日超额  60日超额  120日超额  60日跑赢比例")
        total = (mask & (stage > 0)).sum()
        for code, label in NAMES.items():
            sel = mask & (stage == code)
            e20, e60, e120 = (np.nanmean(fwd[h][1][sel]) for h in (20, 60, 120))
            hit = np.nanmean(fwd[60][1][sel] > 0)
            print(f"{label}  {sel.sum() / total:6.1%}  {e20:+8.2%}  {e60:+8.2%}  {e120:+8.2%}   {hit:6.1%}")

        prev = np.vstack([np.zeros((1, stage.shape[1]), dtype=np.int8), stage[:-1]])
        print("切换           次数    20日超额  60日超额  120日超额  60日跑赢比例")
        for a, b in ((BASE, ADVANCE), (TOP, ADVANCE), (ADVANCE, TOP), (TOP, DECLINE), (DECLINE, BASE), (BASE, DECLINE)):
            sel = mask & (prev == a) & (stage == b)
            e20, e60, e120 = (np.nanmean(fwd[h][1][sel]) for h in (20, 60, 120))
            hit = np.nanmean(fwd[60][1][sel] > 0)
            print(f"{NAMES[a][2:]}→{NAMES[b][2:]}      {sel.sum():6d}  {e20:+8.2%}  {e60:+8.2%}  {e120:+8.2%}   {hit:6.1%}")

        # Stage-2 entries (from base) split by the CSI 300's own stage on that day.
        entries = mask & (prev == BASE) & (stage == ADVANCE)
        print("筑底→右侧 按沪深300所处阶段   次数    60日超额  60日绝对收益")
        for code, label in NAMES.items():
            sel = entries & (index_stage[:, None] == code)
            print(f"  沪深300 {label}             {sel.sum():6d}  {np.nanmean(fwd[60][1][sel]):+8.2%}  "
                  f"{np.nanmean(fwd[60][0][sel]):+8.2%}")

        # Long-only portfolio: equal weight over stocks in stage 2 (decided at t, earned on t+1).
        held = (stage == ADVANCE) & listed
        gated = held & (index_stage == ADVANCE)[:, None]
        rows = []
        for label, book in (("持有全部右侧股", held), ("仅沪深300也右侧时持有", gated)):
            weight = book[:-1].astype(float)
            n = weight.sum(axis=1)
            ret = np.where(n > 0, np.nansum(weight * np.nan_to_num(daily[1:]), axis=1) / np.maximum(n, 1), 0.0)
            rows.append((label, ret))
        bench = np.nan_to_num(market_daily[1:])
        years = sessions.dt.year.to_numpy()[1:]
        print("年份     全市场等权   " + "   ".join(label for label, _ in rows))
        for year in range(2021, 2027):
            sel = (years == year) & (np.arange(len(years)) >= start)
            line = f"{year}     {np.prod(1 + bench[sel]) - 1:+8.1%}   "
            line += "   ".join(f"{np.prod(1 + r[sel]) - 1:+14.1%}" for _, r in rows)
            index_share = {NAMES[c][2:]: np.mean(index_stage[1:][sel] == c) for c in NAMES}
            print(line + "   沪深300阶段占比 " + " ".join(f"{k}{v:.0%}" for k, v in index_share.items()))
        for label, r in rows:
            sel = np.arange(len(r)) >= start
            wealth = np.cumprod(1 + r[sel])
            dd = (wealth / np.maximum.accumulate(wealth) - 1).min()
            bench_w = np.cumprod(1 + bench[sel])
            print(f"{label}: 累计 {wealth[-1] - 1:+.1%}（全市场等权 {bench_w[-1] - 1:+.1%}），最大回撤 {dd:.1%}，"
                  f"平均持股 {np.mean([b.sum() for b in (held if label.startswith('持有') else gated)[start:-1]]):.0f} 只")


if __name__ == "__main__":
    main()
