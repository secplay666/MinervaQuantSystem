"""Checks of the 2026-10-24 live stream ("今年最后一波怎么搞"): the A-share / Nasdaq ratio rule, his 7-17 exit, the
宁德时代 trade.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/macro/stream_1024_checks.py data/market.duckdb \
        ~/L1/research/us_markets/data/us_indices.parquet

Ratio rule, fixed beforehand: the ratio (A-share index / Nasdaq Composite, both in their own currency) closes at a
new 250-session high after 120 sessions without one = "breakout"; the A-share index over the next 6 and 12
months, against all days.  Also the rule he describes on the chart (a breakout of the ratio dates the start of a
bull market) checked against the bull markets of the 20% rule.
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd


def main() -> None:
    con = duckdb.connect(sys.argv[1], read_only=True)
    a = con.execute("SELECT symbol, trade_date, close FROM index_bars WHERE symbol IN ('sz399006', 'sh000001', 'sh000300') "
                    "ORDER BY 2").fetchdf()
    a["trade_date"] = pd.to_datetime(a["trade_date"])
    a = {s: g.set_index("trade_date")["close"].astype(float) for s, g in a.groupby("symbol")}
    us = pd.read_parquet(sys.argv[2])
    ixic = us[us["symbol"] == ".IXIC"].set_index("trade_date")["close"].astype(float)

    for sym, name in (("sz399006", "创业板指"), ("sh000001", "上证指数")):
        s = a[sym]
        ratio = (s / ixic.reindex(s.index).ffill()).dropna()
        high = ratio.rolling(250, min_periods=250).max().shift(1)
        new_high = ratio > high
        events, last = [], -10**9
        for i, flag in enumerate(new_high.to_numpy()):
            if flag and i - last > 120:
                events.append(ratio.index[i])
            if flag:
                last = i
        f6, f12 = s.shift(-125) / s - 1, s.shift(-250) / s - 1
        base6, base12 = f6.loc[ratio.index[250]:].dropna(), f12.loc[ratio.index[250]:].dropna()
        print(f"\n== {name} / 纳斯达克 比值：创一年新高（之前半年没有新高）{len(events)} 次，{ratio.index[0].date()} 起")
        for d in events:
            print(f"  {d.date()}：之后 6 个月 {f6.get(d, np.nan):+.0%}，12 个月 {f12.get(d, np.nan):+.0%}")
        e6, e12 = f6.reindex(events).dropna(), f12.reindex(events).dropna()
        print(f"  信号后平均 6 个月 {e6.mean():+.1%}（上涨 {(e6 > 0).mean():.0%}），12 个月 {e12.mean():+.1%}"
              f"（上涨 {(e12 > 0).mean():.0%}）；所有交易日 6 个月 {base6.mean():+.1%}（{(base6 > 0).mean():.0%}），"
              f"12 个月 {base12.mean():+.1%}（{(base12 > 0).mean():.0%}）")
        print(f"  比值现在：离一年高点 {ratio.iloc[-1] / ratio.iloc[-250:].max() - 1:+.0%}，离 2015 年以来高点 "
              f"{ratio.iloc[-1] / ratio.loc['2015':].max() - 1:+.0%}")

    print("\n== 他说 7-17 清仓")
    for sym, name in (("sz399006", "创业板指"), ("sh000300", "沪深300")):
        s = a[sym].loc["2026"]
        peak = s.idxmax()
        x = s.asof(pd.Timestamp("2026-07-17"))
        print(f"{name}：高点 {s.max():.0f}（{peak.date()}）→ 7-17 {x:.0f}（{x / s.max() - 1:+.0%}）→ 10-23 {s.iloc[-1]:.0f}"
              f"（7-17 之后 {s.iloc[-1] / x - 1:+.0%}）；9-30 {s.asof(pd.Timestamp('2026-09-30')):.0f}")

    bar = con.execute("SELECT trade_date, open, close, high, low FROM daily_bars WHERE symbol = '300750' "
                      "ORDER BY trade_date DESC LIMIT 3").fetchdf()
    print("\n== 宁德时代 最近三天\n" + bar.to_string(index=False))


if __name__ == "__main__":
    main()
