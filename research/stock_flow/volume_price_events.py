"""Volume-price events of individual stocks as a proxy for "big money coming in / going out", and the short-term
reversal (question of 2026-10-25).  Order-size money flow (Eastmoney's 主力资金) could not be downloaded, so the
retail reading of it is tested from daily bars: a surge in turnover on a big up day ("主力进场") or down day
("主力出逃").

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/volume_price_events.py data/market.duckdb

Rules, fixed beforehand.  2021-01 to 2026-10, each day the stocks with a 20-session average turnover of 100 million
yuan or more.  At day t's close:
  放量上涨  turnover >= 2x its 20-session average (t-20..t-1) and the day's return >= +3%
  放量下跌  the same turnover and return <= -3%
  5 日超跌 / 超涨  the 5-session return in the bottom / top 10% of the day's universe
Entry at t+1's open (back-adjusted prices), exit at the close of t+1, t+5, t+20; the result is the excess over the
same window's average of the universe.  Entries whose t+1 open is 9.5% or more above t's close (likely a limit-up
open, not buyable) are dropped.  Costs not included: a round trip costs about 0.15–0.2% (stamp duty 0.05% on the
sale, commissions, slippage).
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

SQL = """
WITH b AS (
  SELECT symbol, trade_date, hfq_open AS o, hfq_close AS c, turnover_cny AS amt,
         hfq_close / lag(hfq_close) OVER w - 1 AS r1,
         hfq_close / lag(hfq_close, 5) OVER w - 1 AS r5,
         avg(turnover_cny) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS amt20,
         lead(hfq_open) OVER w AS o1, lead(hfq_close) OVER w AS c1, lead(hfq_close, 5) OVER w AS c5,
         lead(hfq_close, 20) OVER w AS c20
  FROM daily_bars_adjusted
  WHERE trade_date >= DATE '2020-11-01' AND hfq_close > 0
  WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
)
SELECT * FROM b WHERE trade_date >= DATE '2021-01-01' AND amt20 >= 1e8 AND o1 > 0
"""


def main() -> None:
    con = duckdb.connect(sys.argv[1], read_only=True)
    d = con.execute(SQL).fetchdf()
    d["gap"] = d["o1"] / d["c"] - 1
    for n, col in ((1, "c1"), (5, "c5"), (20, "c20")):
        d[f"f{n}"] = d[col] / d["o1"] - 1
        d[f"x{n}"] = d[f"f{n}"] - d.groupby("trade_date")[f"f{n}"].transform("mean")
    d["abn"] = d["amt"] / d["amt20"]
    d["r5_rank"] = d.groupby("trade_date")["r5"].rank(pct=True)
    tradable = d["gap"] < 0.095
    print(f"{d['trade_date'].min()} – {d['trade_date'].max()}：{len(d):,} 个股票日，平均每天 "
          f"{d.groupby('trade_date').size().mean():.0f} 只（20 日均成交额 ≥ 1 亿）")
    groups = {
        "放量上涨（成交 ≥ 2 倍、涨 ≥ 3%）": (d["abn"] >= 2) & (d["r1"] >= 0.03),
        "放量下跌（成交 ≥ 2 倍、跌 ≥ 3%）": (d["abn"] >= 2) & (d["r1"] <= -0.03),
        "缩量上涨（成交 ≤ 0.7 倍、涨 ≥ 3%）": (d["abn"] <= 0.7) & (d["r1"] >= 0.03),
        "5 日超跌（最差 10%）": d["r5_rank"] <= 0.1,
        "5 日超涨（最好 10%）": d["r5_rank"] >= 0.9,
    }
    rows = []
    for name, mask in groups.items():
        g = d[mask & tradable]
        row = {"信号": name, "次数": len(g), "被涨停开盘剔除": int((mask & ~tradable).sum())}
        for n in (1, 5, 20):
            x = g[f"x{n}"].dropna()
            row[f"{n}日超额"] = x.mean()
            row[f"{n}日跑赢比例"] = (x > 0).mean()
        rows.append(row)
    t = pd.DataFrame(rows)
    fmt = {c: "{:+.2%}".format for c in t.columns if "超额" in c}
    fmt.update({c: "{:.0%}".format for c in t.columns if "比例" in c})
    print(t.to_string(index=False, formatters=fmt))

    print("\n按年（5 日超额）：")
    d["year"] = pd.to_datetime(d["trade_date"]).dt.year
    yearly = {name: d[mask & tradable].groupby("year")["x5"].mean() for name, mask in groups.items()}
    print(pd.DataFrame(yearly).to_string(float_format=lambda v: f"{v:+.2%}"))

    print("\n“跟着放量上涨买”：每天买入当天所有放量上涨的股票，第二天开盘买、持有 5 天，按天等权：")
    g = d[groups["放量上涨（成交 ≥ 2 倍、涨 ≥ 3%）"] & tradable]
    daily = g.groupby("trade_date")["x5"].mean()
    print(f"有信号的交易日 {len(daily)} 个，平均每天 {g.groupby('trade_date').size().mean():.0f} 只；"
          f"5 日超额平均 {daily.mean():+.2%}，扣 0.2% 往返成本后 {daily.mean() - 0.002:+.2%}；"
          f"超额为正的日子 {(daily > 0).mean():.0%}")
    print("\n截面相关（Rank IC，按天平均）：当天涨跌、异常成交 vs 之后（次日开盘起）1、5 日超额")
    for col, name in (("r1", "当天涨跌"), ("abn", "异常成交（倍数）"), ("r5", "5 日涨跌")):
        ics = {}
        for n in (1, 5):
            ic = d.groupby("trade_date").apply(lambda x: x[col].corr(x[f"f{n}"], method="spearman"))
            ics[n] = (ic.mean(), ic.mean() / ic.std() * np.sqrt(len(ic)))
        print(f"{name}：1 日 {ics[1][0]:+.3f}（t {ics[1][1]:.1f}），5 日 {ics[5][0]:+.3f}（t {ics[5][1]:.1f}）")


if __name__ == "__main__":
    main()
