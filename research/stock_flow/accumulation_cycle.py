"""The folk 主力 cycle — quiet accumulation at a low price (低位吸筹), markup, distribution on heavy volume at the top
(高位派发), markdown — written as measurable signatures and tested against what followed.  Question of 2026-10-25.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/accumulation_cycle.py data/market.duckdb

Measures (back-adjusted daily bars, 2015-01 on; signals from 2016):
  cost / 筹码  the holders' average cost: each day a share v of the stock (turnover rate) changes hands at the day's
               close, so cost_t = (1 - v) cost_{t-1} + v P_t (Grinblatt & Han 2005's reference price, the mean of the
               "chip distribution"); its dispersion from the same recursion on P^2: chip_disp = std / cost
  CGO          P / cost - 1: the holders' average unrealised gain (获利盘)
  pos250       where the close sits between its 250-session low (0) and high (1)
  sv60         60-session signed turnover: (turnover on up days - on down days) / all turnover
  heat         20-session average turnover / 250-session average
Universe each day: 20-session average turnover >= 30 million yuan.
Signatures, fixed beforehand (an event at most once per stock in 60 sessions):
  A  低位吸筹   pos250 <= 0.3, |60-session return| <= 10%, chip_disp in the day's lowest 20%, sv60 >= 0.05
  A0 低位横盘   pos250 <= 0.3 and |60-session return| <= 10% (the control: the same base without the signs)
  B  高位派发   120-session return >= 50%, heat >= 2, 20-session return <= 5% (放量滞涨), CGO >= 30%
  B0 大涨之后   120-session return >= 50% (the control)
  B1 放量续涨   120-session return >= 50%, heat >= 2, 20-session return > 15% (markup still going)
Outcomes from the next session's open: the close after 20, 60, 120 sessions as excess over the universe's mean of the
same window; whether the high within 120 sessions reached +30% (a markup) and the low -30% (a markdown).
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

COOLDOWN = 60


def matrices(database: str) -> dict[str, pd.DataFrame]:
    con = duckdb.connect(database, read_only=True)
    d = con.execute("SELECT symbol, trade_date, hfq_open AS o, hfq_high AS h, hfq_low AS l, hfq_close AS c, "
                    "turnover_cny AS amt, turnover_rate_pct AS tr FROM daily_bars_adjusted "
                    "WHERE trade_date >= DATE '2015-01-01' AND hfq_close > 0").fetchdf()
    out = {}
    for col in ("o", "h", "l", "c", "amt", "tr"):
        out[col] = d.pivot(index="trade_date", columns="symbol", values=col).astype("float64")
    return out


def chips(close: pd.DataFrame, turnover_rate: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = close.to_numpy()
    v = np.clip(turnover_rate.to_numpy() / 100.0, 0.0, 0.5)
    mean = np.full(p.shape, np.nan)
    m2 = np.full(p.shape, np.nan)
    cur, cur2 = p[0].copy(), p[0].copy() ** 2
    for t in range(p.shape[0]):
        pt, vt = p[t], v[t]
        start = np.isnan(cur) & ~np.isnan(pt)
        cur[start], cur2[start] = pt[start], pt[start] ** 2
        ok = ~np.isnan(pt) & ~np.isnan(vt)
        cur[ok] = (1 - vt[ok]) * cur[ok] + vt[ok] * pt[ok]
        cur2[ok] = (1 - vt[ok]) * cur2[ok] + vt[ok] * pt[ok] ** 2
        mean[t], m2[t] = cur, cur2
    disp = np.sqrt(np.maximum(m2 - mean ** 2, 0)) / mean
    return (pd.DataFrame(mean, index=close.index, columns=close.columns),
            pd.DataFrame(disp, index=close.index, columns=close.columns))


def first_events(flags: pd.DataFrame) -> list[tuple]:
    out = []
    values = flags.to_numpy()
    for j, symbol in enumerate(flags.columns):
        idx = np.flatnonzero(values[:, j])
        last = -10**9
        for i in idx:
            if i - last > COOLDOWN:
                out.append((i, j))
                last = i
    return out


def main() -> None:
    m = matrices(sys.argv[1])
    c, o, h, l, amt = m["c"], m["o"], m["h"], m["l"], m["amt"]
    cost, disp = chips(c, m["tr"])
    cgo = c / cost - 1
    hi250, lo250 = c.rolling(250, min_periods=200).max(), c.rolling(250, min_periods=200).min()
    pos = (c - lo250) / (hi250 - lo250)
    r20, r60, r120 = c / c.shift(20) - 1, c / c.shift(60) - 1, c / c.shift(120) - 1
    sign = np.sign(c.pct_change())
    sv60 = (sign * amt).rolling(60, min_periods=50).sum() / amt.rolling(60, min_periods=50).sum()
    amt20 = amt.rolling(20, min_periods=15).mean()
    heat = amt20 / amt.rolling(250, min_periods=200).mean()
    universe = amt20 >= 3e7
    o1 = o.shift(-1)
    fwd = {n: c.shift(-n) / o1 - 1 for n in (20, 60, 120)}
    excess = {n: f.sub(f.where(universe).mean(axis=1), axis=0) for n, f in fwd.items()}
    fwd_hi = h[::-1].rolling(120, min_periods=60).max()[::-1].shift(-1) / o1 - 1
    fwd_lo = l[::-1].rolling(120, min_periods=60).min()[::-1].shift(-1) / o1 - 1
    disp_rank = disp.where(universe).rank(axis=1, pct=True)
    start = c.index >= pd.Timestamp("2016-01-01")

    base_low = universe & (pos <= 0.3) & (r60.abs() <= 0.10)
    big_run = universe & (r120 >= 0.5)
    sig = {
        "A  低位吸筹": base_low & (disp_rank <= 0.2) & (sv60 >= 0.05),
        "A0 低位横盘（对照）": base_low,
        "B  高位派发（放量滞涨）": big_run & (heat >= 2) & (r20 <= 0.05) & (cgo >= 0.3),
        "B0 大涨之后（对照）": big_run,
        "B1 放量续涨": big_run & (heat >= 2) & (r20 > 0.15),
    }
    rows, per_year = [], {}
    all_x = {n: excess[n].where(universe).to_numpy()[start] for n in (20, 60, 120)}
    hi_all = fwd_hi.where(universe).to_numpy()[start]
    lo_all = fwd_lo.where(universe).to_numpy()[start]
    rows.append({"信号": "全部股票日", "次数": int(np.isfinite(all_x[20]).sum()),
                 **{f"{n}日超额": np.nanmean(all_x[n]) for n in (20, 60, 120)},
                 "120日内涨过30%": np.nanmean(hi_all >= 0.3), "120日内跌过30%": np.nanmean(lo_all <= -0.3)})
    for name, flags in sig.items():
        ev = [(i, j) for i, j in first_events(flags.fillna(False) & np.asarray(start)[:, None]) if i + 1 < len(c)]
        ii, jj = np.array([e[0] for e in ev]), np.array([e[1] for e in ev])
        row = {"信号": name, "次数": len(ev)}
        for n in (20, 60, 120):
            row[f"{n}日超额"] = np.nanmean(excess[n].to_numpy()[ii, jj])
        row["120日内涨过30%"] = np.nanmean(fwd_hi.to_numpy()[ii, jj] >= 0.3)
        row["120日内跌过30%"] = np.nanmean(fwd_lo.to_numpy()[ii, jj] <= -0.3)
        rows.append(row)
        years = pd.Series(excess[60].to_numpy()[ii, jj], index=c.index[ii].year)
        per_year[name] = years.groupby(level=0).mean()
    t = pd.DataFrame(rows)
    fmt = {k: "{:+.2%}".format for k in t.columns if "超额" in k}
    fmt.update({"120日内涨过30%": "{:.0%}".format, "120日内跌过30%": "{:.0%}".format})
    print(t.to_string(index=False, formatters=fmt))
    print("\n按年（60 日超额）：")
    print(pd.DataFrame(per_year).to_string(float_format=lambda v: f"{v:+.1%}"))

    # the same measures as continuous predictors: cross-sectional rank IC with the 60-session excess, monthly
    month_ends = c.groupby(c.index.to_period("M")).apply(lambda x: x.index[-1]).to_numpy()
    month_ends = [d for d in month_ends if d >= pd.Timestamp("2016-01-01")]
    print("\n月末截面 Rank IC（对之后 60 日超额，月度平均，t 值按月度序列）：")
    for name, x in (("获利盘 CGO", cgo), ("筹码集中度（离散度，越大越分散）", disp), ("60 日资金方向 sv60", sv60),
                    ("价格位置 pos250", pos), ("成交热度", heat)):
        ics = []
        for d in month_ends:
            a, b, u = x.loc[d], excess[60].loc[d], universe.loc[d]
            j = pd.concat([a[u], b[u]], axis=1).dropna()
            if len(j) > 100:
                ics.append(j.iloc[:, 0].corr(j.iloc[:, 1], method="spearman"))
        ics = np.array(ics)
        print(f"  {name}：{ics.mean():+.3f}（t {ics.mean() / ics.std() * np.sqrt(len(ics)):.1f}，{len(ics)} 个月）")


if __name__ == "__main__":
    main()
