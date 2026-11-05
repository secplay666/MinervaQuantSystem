"""How to buy after a deep fall (question of 2026-11-05: in cash, thinking of 抄底).  Compares entry rules on the
broad-rally days in a drawdown of bottom_after_rally.py (CSI 300 >= +2%, >= 80% of A shares up, >= 15% below the
250-session high; first per 10 sessions), 2005-2026, on the index itself (an index ETF, costs ignored):

  A 一次买满        all at the next open
  B 分三批          a third at the next open, and after 20 and 40 sessions
  C 一次买满+止损    as A; out for good at the first close below the low of the 20 sessions before the event
  D 分三批+止损      as B with C's exit (no more buying after it)
  E 站上60日线再买   all at the first close above the 60-session average (next open), else stay in cash

Outcome after 120 sessions: return on the whole capital (cash earns nothing), its worst drawdown along the way.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python <this file> [sh000300|sh000905|sz399006]
"""

import sys

import duckdb
import numpy as np
import pandas as pd

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "sh000300"
HORIZON = 120
con = duckdb.connect("/home/van/L1/MinervaQuantSystem/data/market.duckdb", read_only=True)
breadth = con.execute("""SELECT trade_date, avg((pct_change > 0)::int) up FROM daily_bars
                         WHERE pct_change IS NOT NULL GROUP BY 1 HAVING count(*) > 500""").fetchdf()
breadth = breadth.set_index(pd.to_datetime(breadth["trade_date"]))["up"]
d = con.execute("SELECT trade_date, open, close FROM index_bars WHERE symbol = ? ORDER BY trade_date", [SYMBOL]).fetchdf()
d.index = pd.to_datetime(d["trade_date"])
o, c = d["open"].to_numpy(), d["close"].to_numpy()
r = d["close"].pct_change()
dd = d["close"] / d["close"].rolling(250, min_periods=120).max() - 1
ma60 = d["close"].rolling(60).mean().to_numpy()
rise = {"sh000300": 0.02, "sh000905": 0.035, "sz399006": 0.04}[SYMBOL]
depth = {"sh000300": 0.15, "sh000905": 0.20, "sz399006": 0.25}[SYMBOL]
flag = ((r >= rise) & (breadth.reindex(d.index) >= 0.8) & (dd <= -depth)).to_numpy()


def run(i: int, buys: list[tuple[int, float]], stop: float | None) -> tuple[float, float]:
    """Value path of 1 unit of capital from session i+1 to i+HORIZON; buys = (session, share of capital) at that open."""
    cash, units, peak, worst, stopped = 1.0, 0.0, 1.0, 0.0, False
    plan = dict(buys)
    for k in range(i + 1, i + HORIZON + 1):
        if not stopped and k in plan:
            spend = min(cash, plan[k])
            units += spend / o[k]
            cash -= spend
        value = cash + units * c[k]
        if stop is not None and not stopped and units > 0 and c[k] < stop:
            stopped = True
            cash, units = value, 0.0          # out at this close
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return cash + units * c[i + HORIZON] - 1, worst


rows, last = [], -10**9
for i in np.flatnonzero(flag):
    if i - last <= 10 or i + HORIZON >= len(c):
        continue
    last = i
    low = c[max(0, i - 20):i].min()
    third = 1 / 3
    above = next((k for k in range(i + 1, i + HORIZON - 1) if c[k] > ma60[k]), None)
    plans = {"A 一次买满": ([(i + 1, 1.0)], None), "B 分三批": ([(i + 1, third), (i + 21, third), (i + 41, 1.0)], None),
             "C 一次买满+止损": ([(i + 1, 1.0)], low), "D 分三批+止损": ([(i + 1, third), (i + 21, third), (i + 41, 1.0)], low),
             "E 站上60日线再买": ([(above + 1, 1.0)] if above is not None else [], None)}
    row = {"date": d.index[i]}
    for name, (buys, stop) in plans.items():
        row[name], row[name + " 回撤"] = run(i, buys, stop)
    rows.append(row)
t = pd.DataFrame(rows)
names = ["A 一次买满", "B 分三批", "C 一次买满+止损", "D 分三批+止损", "E 站上60日线再买"]
print(f"{SYMBOL}：{len(t)} 次（{t['date'].min():%Y}–{t['date'].max():%Y}），每次之后 {HORIZON} 个交易日")
out = pd.DataFrame({n: {"平均收益": t[n].mean(), "中位数": t[n].median(), "盈利的比例": (t[n] > 0).mean(),
                        "最差 10%": t[n].quantile(0.1), "最差一次": t[n].min(),
                        "过程最大回撤（中位数）": t[n + " 回撤"].median(), "过程最大回撤（最差 10%）": t[n + " 回撤"].quantile(0.1)}
                    for n in names}).T
print(out.to_string(formatters={k: "{:+.1%}".format for k in out.columns if k != "盈利的比例"} | {"盈利的比例": "{:.0%}".format}))
