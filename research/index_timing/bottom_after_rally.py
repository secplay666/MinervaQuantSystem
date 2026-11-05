"""Run on the server: cd ~/L1/MinervaQuantSystem && .venv/bin/python <this file> [--table]  (question of 2026-11-04).

Was a broad rally after a deep fall the bottom?  Event study on 2005-2026 (production catalog, read-only).

Events: a session where the index rose >= RISE, >= BREADTH of A shares rose, and the index closed >= DEPTH below its
250-session high.  First event per COOLDOWN sessions.  Outcomes: the index return 5/20/60/120 sessions later; whether
the low of the 20 sessions before the event (the "bottom" it would be) held — no close below it within the next 60 /
120 sessions; the worst close in the next 60 sessions relative to the event close.
"""

import sys

import duckdb
import numpy as np
import pandas as pd

con = duckdb.connect("/home/van/L1/MinervaQuantSystem/data/market.duckdb", read_only=True)
breadth = con.execute("""
    SELECT trade_date, avg((pct_change > 0)::int) up, count(*) n FROM daily_bars
    WHERE pct_change IS NOT NULL AND trade_date >= DATE '2005-01-01' GROUP BY 1 HAVING count(*) > 500 ORDER BY 1""").fetchdf()
breadth["trade_date"] = pd.to_datetime(breadth["trade_date"])
breadth = breadth.set_index("trade_date")["up"]
COOLDOWN = 10


def study(symbol: str, name: str, rise: float, breadth_min: float, depth: float) -> pd.DataFrame:
    d = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY trade_date", [symbol]).fetchdf()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    c = d.set_index("trade_date")["close"]
    r = c.pct_change()
    dd = c / c.rolling(250, min_periods=120).max() - 1
    up = breadth.reindex(c.index)
    flag = (r >= rise) & (up >= breadth_min) & (dd <= -depth)
    idx = np.flatnonzero(flag.to_numpy())
    events, last = [], -10**9
    values = c.to_numpy()
    for i in idx:
        if i - last <= COOLDOWN:
            continue
        last = i
        prior_low = values[max(0, i - 20):i].min()
        row = {"date": c.index[i], "ret": r.iloc[i], "up": up.iloc[i], "dd": dd.iloc[i]}
        for n in (5, 20, 60, 120):
            row[f"f{n}"] = values[i + n] / values[i] - 1 if i + n < len(values) else np.nan
        for n in (60, 120):
            window = values[i + 1:i + 1 + n]
            row[f"held{n}"] = float(window.min() >= prior_low) if len(window) == n else np.nan
        window = values[i + 1:i + 61]
        row["worst60"] = window.min() / values[i] - 1 if len(window) == 60 else np.nan
        events.append(row)
    e = pd.DataFrame(events)
    # the same index, all sessions in a drawdown of at least `depth` (the control)
    base = pd.DataFrame({"f20": c.shift(-20) / c - 1, "f60": c.shift(-60) / c - 1, "dd": dd}).dropna()
    base = base[base["dd"] <= -depth]
    print(f"\n== {name}：当日涨 ≥{rise:.0%}、上涨家数 ≥{breadth_min:.0%}、距 250 日高点跌 ≥{depth:.0%}：{len(e)} 次"
          f"（对照：同样深跌中的任意一天，之后 20 日 {base['f20'].mean():+.1%}、60 日 {base['f60'].mean():+.1%}）")
    if e.empty:
        return e
    done = e.dropna(subset=["held60"])
    print(f"   之后 5/20/60/120 日平均：{e['f5'].mean():+.1%} / {e['f20'].mean():+.1%} / {e['f60'].mean():+.1%} / "
          f"{e['f120'].mean():+.1%}；60 日上涨的比例 {(e['f60'].dropna() > 0).mean():.0%}")
    print(f"   前 20 日低点 60 日内守住 {done['held60'].mean():.0%}（{int(done['held60'].sum())}/{len(done)}），120 日内守住 "
          f"{e['held120'].dropna().mean():.0%}；60 日内最深再跌（中位数）{e['worst60'].median():+.1%}")
    for lo, hi in ((0.15, 0.25), (0.25, 0.40), (0.40, 1.0)):
        part = done[(-done["dd"] >= lo) & (-done["dd"] < hi)]
        if len(part):
            print(f"   回撤 {lo:.0%}–{hi:.0%}：{len(part)} 次，60 日内守住 {part['held60'].mean():.0%}，60 日平均 {part['f60'].mean():+.1%}")
    if "--table" not in sys.argv:
        return e
    show = e.copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    print(show.to_string(index=False, formatters={"ret": "{:+.1%}".format, "up": "{:.0%}".format, "dd": "{:+.0%}".format,
                                                 **{f"f{n}": "{:+.1%}".format for n in (5, 20, 60, 120)},
                                                 "worst60": "{:+.1%}".format}))
    return e


study("sh000300", "沪深300（今日 +2.3%、宽度 84%、回撤 -19%）", 0.02, 0.80, 0.15)
study("sh000905", "中证500（今日 +4.7%、回撤 -27%）", 0.035, 0.80, 0.20)
study("sz399006", "创业板指（今日 +6.4%、回撤 -42%）", 0.04, 0.80, 0.25)
