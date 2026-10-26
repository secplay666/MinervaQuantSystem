"""大股东减持 after a run-up: the visible form of distribution.  Question of 2026-10-25.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/insider_selling.py data/market.duckdb

Events: holder_changes (Eastmoney, company announcements of 股东增减持), the first notice per stock in 20 sessions,
direction 减持 or 增持.  Entry at the open of the first session after the notice date; outcome the excess over the
universe (20-session average turnover >= 30 million yuan) of the close 20, 60 and 120 sessions later.  Grouped by
the stock's 120-session return before the notice (fixed buckets: < 0, 0–50%, >= 50%).
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd


def main() -> None:
    con = duckdb.connect(sys.argv[1], read_only=True)
    ch = con.execute("SELECT symbol, direction, notice_date, change_pct_total FROM holder_changes "
                     "WHERE notice_date >= DATE '2016-01-01'").fetchdf()
    ch["notice_date"] = pd.to_datetime(ch["notice_date"])
    bars = con.execute("SELECT symbol, trade_date, hfq_open AS o, hfq_close AS c, turnover_cny AS amt "
                       "FROM daily_bars_adjusted WHERE trade_date >= DATE '2015-06-01' AND hfq_close > 0").fetchdf()
    o = bars.pivot(index="trade_date", columns="symbol", values="o")
    c = bars.pivot(index="trade_date", columns="symbol", values="c")
    amt = bars.pivot(index="trade_date", columns="symbol", values="amt")
    dates = pd.DatetimeIndex(c.index)
    universe = amt.rolling(20, min_periods=15).mean() >= 3e7
    r120 = c / c.shift(120) - 1
    excess = {}
    for n in (20, 60, 120):
        f = c.shift(-(n - 1)) / o - 1
        excess[n] = f.sub(f.where(universe).mean(axis=1), axis=0)
    cols = {s: k for k, s in enumerate(c.columns)}
    rows, last = [], {}
    for r in ch.sort_values("notice_date").itertuples(index=False):
        i = dates.searchsorted(r.notice_date, side="right")
        j = cols.get(r.symbol)
        if j is None or i >= len(dates) or i == 0 or not universe.iat[i, j]:
            continue
        key = (r.symbol, r.direction)
        if key in last and i - last[key] <= 20:
            continue
        last[key] = i
        rows.append({"direction": r.direction, "r120": r120.iat[i - 1, j], "size": r.change_pct_total,
                     **{f"x{n}": excess[n].iat[i, j] for n in (20, 60, 120)}})
    t = pd.DataFrame(rows).dropna(subset=["r120", "x20"])
    t["前120日"] = pd.cut(t["r120"], [-10, 0, 0.5, 100], labels=["下跌", "涨 0–50%", "涨 50% 以上"])
    g = t.groupby(["direction", "前120日"], observed=True).agg(次数=("x20", "size"), 超额20日=("x20", "mean"),
                                                             超额60日=("x60", "mean"), 超额120日=("x120", "mean"),
                                                             跑赢比例60日=("x60", lambda x: (x.dropna() > 0).mean()))
    print(f"{len(t):,} 个公告事件（2016 年起，同一股票同方向 20 个交易日内只算一次）")
    print(g.to_string(formatters={"超额20日": "{:+.2%}".format, "超额60日": "{:+.2%}".format,
                                  "超额120日": "{:+.2%}".format, "跑赢比例60日": "{:.0%}".format}))


if __name__ == "__main__":
    main()
