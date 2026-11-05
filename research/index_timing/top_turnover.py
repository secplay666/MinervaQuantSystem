"""Stocks with the highest turnover (成交额) on a day, and whether buying them had an edge (question of 2026-11-05).

    cd ~/L1/MinervaQuantSystem && .venv/bin/python <this file> 2026-11-04

Part 1: the day's top stocks by turnover.  Part 2, fixed beforehand, 2016-2026: each session, the TOP stocks by
turnover (and, separately, by turnover relative to their own 20-session average) among A shares with a 20-session
average turnover >= 30 million yuan (BSE excluded); bought at the next session's open, held 5 / 20 / 60 sessions;
excess over the equal-weighted universe of the same window.  Overall, and on the broad-rally days of
bottom_after_rally.py (CSI 300 >= +2%, >= 80% of A shares up, CSI 300 >= 15% below its 250-session high).
"""

import sys

import duckdb
import numpy as np
import pandas as pd

DAY = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2026-11-04")
TOP = 50
con = duckdb.connect("/home/van/L1/MinervaQuantSystem/data/market.duckdb", read_only=True)

# -- part 1: the day ------------------------------------------------------------------------------------------
day = con.execute(f"""
    WITH h AS (SELECT symbol, trade_date, turnover_cny,
                      avg(turnover_cny) OVER (PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) avg20
               FROM daily_bars WHERE trade_date >= DATE '{DAY:%Y-%m-%d}' - INTERVAL 60 DAY)
    SELECT b.symbol, m.name, i.l1_name AS industry, b.close, b.pct_change, b.turnover_cny / 1e8 AS amount_yi,
           h.turnover_cny / h.avg20 AS vs20, b.turnover_rate_pct
    FROM daily_bars b JOIN h USING (symbol, trade_date) JOIN security_master m USING (symbol)
    LEFT JOIN industry_sw i ON i.symbol = b.symbol AND i.end_date IS NULL
    WHERE b.trade_date = DATE '{DAY:%Y-%m-%d}' ORDER BY b.turnover_cny DESC""").fetchdf()
total = day["amount_yi"].sum()
print(f"{DAY:%Y-%m-%d} 全市场成交 {total:,.0f} 亿；前 {TOP} 只合计 {day['amount_yi'].head(TOP).sum():,.0f} 亿"
      f"（{day['amount_yi'].head(TOP).sum() / total:.0%}），其中上涨 {(day['pct_change'].head(TOP) > 0).sum()} 只，"
      f"涨幅中位数 {day['pct_change'].head(TOP).median():+.1f}%（全市场 {day['pct_change'].median():+.1f}%）")
print(day.head(30).to_string(index=False, formatters={"pct_change": "{:+.1f}%".format, "amount_yi": "{:.0f}".format,
                                                      "vs20": "{:.1f}".format, "turnover_rate_pct": "{:.1f}%".format,
                                                      "close": "{:.2f}".format}))
print("前 50 只的行业：", day.head(TOP)["industry"].value_counts().head(8).to_dict())

# -- part 2: history -----------------------------------------------------------------------------------------------
bars = con.execute("""
    SELECT b.symbol, b.trade_date, b.hfq_open o, b.hfq_close c, b.turnover_cny amt, b.pct_change pct
    FROM daily_bars_adjusted b JOIN security_master m USING (symbol)
    WHERE b.trade_date >= DATE '2015-06-01' AND b.hfq_close > 0 AND m.board <> 'BSE'""").fetchdf()
o, c, amt, pct = (bars.pivot(index="trade_date", columns="symbol", values=k) for k in ("o", "c", "amt", "pct"))
dates = pd.DatetimeIndex(pd.to_datetime(c.index))
avg20 = amt.rolling(20, min_periods=15).mean()
universe = avg20.shift(1) >= 3e7
rel = amt / avg20.shift(1)
start = dates >= pd.Timestamp("2016-01-01")


def forward(n: int) -> pd.DataFrame:
    f = c.shift(-n) / o.shift(-1) - 1           # buy at the next open, hold to the close n sessions later
    return f.sub(f.where(universe).mean(axis=1), axis=0)


fwd = {n: forward(n) for n in (5, 20, 60)}
amt_rank = amt.where(universe).rank(axis=1, ascending=False)
rel_rank = rel.where(universe).rank(axis=1, ascending=False)


def summary(name: str, picks: pd.DataFrame, rows: np.ndarray) -> None:
    out = [name]
    for n in (5, 20, 60):
        daily = fwd[n].where(picks).mean(axis=1).to_numpy()[rows]
        daily = daily[np.isfinite(daily)]
        step = daily[::n]                         # non-overlapping windows for the t value
        t = step.mean() / step.std() * np.sqrt(len(step)) if len(step) > 2 else np.nan
        out.append(f"{n} 日 {daily.mean():+.2%}（t {t:.1f}，{(daily > 0).mean():.0%} 的日子为正）")
    print("  " + "；".join(out))


rows_all = np.flatnonzero(start)
print(f"\n2016–2026 每天买入前 {TOP} 只，相对等权股票池的超额（次日开盘买入）：")
summary(f"成交额最高 {TOP} 只", amt_rank <= TOP, rows_all)
summary(f"成交额 / 自身 20 日均值最高 {TOP} 只", rel_rank <= TOP, rows_all)
summary(f"成交额最高 {TOP} 只且当天涨 ≥5%", (amt_rank <= TOP) & (pct >= 5), rows_all)

# broad-rally days in a drawdown (as in bottom_after_rally.py, every qualifying day)
ix = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY trade_date").fetchdf()
ix = ix.set_index(pd.to_datetime(ix["trade_date"]))["close"]
up_share = (pct > 0).where(pct.notna()).mean(axis=1)
r300 = ix.pct_change().reindex(dates)
dd300 = (ix / ix.rolling(250, min_periods=120).max() - 1).reindex(dates)
rally = (r300 >= 0.02) & (up_share.reindex(dates) >= 0.8) & (dd300 <= -0.15) & start
rows_rally = np.flatnonzero(rally.to_numpy())
rows_rally = rows_rally[rows_rally < len(dates) - 61]
print(f"\n像 11-04 这样的普涨大阳线日（{len(rows_rally)} 天）买入：")
summary(f"成交额最高 {TOP} 只", amt_rank <= TOP, rows_rally)
summary(f"成交额 / 自身 20 日均值最高 {TOP} 只", rel_rank <= TOP, rows_rally)
summary(f"成交额最高 {TOP} 只且当天涨 ≥5%", (amt_rank <= TOP) & (pct >= 5), rows_rally)
summary("当天涨 ≥9%（含涨停）", pct >= 9, rows_rally)

# by year, the plain top-turnover rule (20 sessions)
s = fwd[20].where(amt_rank <= TOP).mean(axis=1)[start]
print("\n成交额最高 50 只，按年（20 日超额，日均）：",
      " ".join(f"{y}:{v:+.1%}" for y, v in s.groupby(s.index.year).mean().items()))
