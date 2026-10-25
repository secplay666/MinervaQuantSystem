"""Can the day's ETF creations (published after the close) be estimated during the session?  Question of 2026-10-25.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/index_timing/intraday_creation.py data/market.duckdb

Daily, 2016-10 on, the four large CSI 300 ETFs (510300, 510310, 510330, 159919) added up: net creation in yuan
(share change x close) against what is visible before the close: the CSI 300 return, the ETFs' turnover
relative to their 20-session average, and the ETFs' close relative to the index (a premium proxy: the change of
ETF / index from the previous day; the ETFs track the price index plus dividends).  Then the sessions collected
intraday since 2026-10-08 (data/canonical/intraday_bars): turnover by hour on the days with large creations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ETFS = ["510300", "510310", "510330", "159919"]


def main() -> None:
    database = Path(sys.argv[1])
    con = duckdb.connect(str(database), read_only=True)
    marks = ",".join(f"'{s}'" for s in ETFS)
    sh = con.execute(f"SELECT symbol, trade_date, shares FROM etf_shares WHERE symbol IN ({marks})").fetchdf()
    px = con.execute(f"SELECT symbol, trade_date, close, turnover_cny FROM etf_bars WHERE symbol IN ({marks})").fetchdf()
    idx = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300'").fetchdf()
    for f in (sh, px, idx):
        f["trade_date"] = pd.to_datetime(f["trade_date"])
    hs = idx.set_index("trade_date")["close"].sort_index()
    shares = sh.pivot_table(index="trade_date", columns="symbol", values="shares").reindex(hs.index).ffill(limit=3)
    close = px.pivot_table(index="trade_date", columns="symbol", values="close").reindex(hs.index)
    turn = px.pivot_table(index="trade_date", columns="symbol", values="turnover_cny").reindex(hs.index)
    d = pd.DataFrame(index=hs.index)
    d["creation"] = (shares.diff() * close).sum(axis=1, min_count=4) / 1e8  # 亿元
    d["ret"] = hs.pct_change()
    d["turn"] = turn.sum(axis=1, min_count=4)
    d["turn_rel"] = d["turn"] / d["turn"].rolling(20).mean().shift(1)
    ratio = (close["510300"] / hs)
    d["prem_chg"] = ratio.pct_change() * 1e4  # bp: the ETF beat (+) or lagged (-) the index that day
    d = d.loc["2016-10-10":].dropna()
    print(f"{len(d)} 个交易日（{d.index.min().date()}–{d.index.max().date()}）；四只沪深300 ETF 合计")
    big = d["creation"] >= d["creation"].quantile(0.95)
    bigout = d["creation"] <= d["creation"].quantile(0.05)
    print(f"大额申购（前 5%，≥ {d['creation'].quantile(0.95):.1f} 亿元）{big.sum()} 天；大额赎回（后 5%，≤ "
          f"{d['creation'].quantile(0.05):.1f} 亿元）{bigout.sum()} 天")
    print("\n当天净申购 与 盘中可见指标 的相关系数（Spearman）：")
    for col, name in (("ret", "沪深300 当天涨跌"), ("turn_rel", "ETF 成交额 / 20 日均值"), ("prem_chg", "510300 相对指数的当天强弱")):
        print(f"  {name}：{d['creation'].corr(d[col], method='spearman'):+.2f}；"
              f"2023 年以来 {d.loc['2023':, 'creation'].corr(d.loc['2023':, col], method='spearman'):+.2f}")
    print("\n大额申购日和其他日子，盘中可见指标的对比（中位数）：")
    for label, mask in (("大额申购日", big), ("大额赎回日", bigout), ("其他日子", ~big & ~bigout)):
        g = d[mask]
        print(f"  {label}：沪深300 {g['ret'].median():+.2%}（下跌的占 {(g['ret'] < 0).mean():.0%}），成交额是 20 日均值的 "
              f"{g['turn_rel'].median():.2f} 倍，510300 相对指数 {g['prem_chg'].median():+.1f} 基点")
    print("\n如果盘中用“ETF 成交额放大到 1.5 倍以上”去猜当天大额申购：")
    for lo, extra in ((1.5, None), (2.0, None), (1.5, "down"), (2.0, "down")):
        flag = d["turn_rel"] >= lo
        if extra == "down":
            flag &= d["ret"] < 0
        hit = big[flag].mean() if flag.any() else np.nan
        cover = flag[big].mean()
        print(f"  成交 ≥ {lo} 倍{'且指数下跌' if extra else ''}：{flag.sum()} 天，其中大额申购 {hit:.0%}（随便一天 5%）；"
              f"大额申购日里有 {cover:.0%} 满足这个条件")

    # the sessions collected intraday
    root = database.parent / "canonical" / "intraday_bars"
    rows = []
    for part in sorted(root.glob("trade_date=*")):
        day = pd.Timestamp(part.name.split("=", 1)[1])
        b = pd.read_parquet(part / "data.parquet")
        b = b[b["symbol"].isin(ETFS + ["sh000300"])]
        if b.empty:
            continue
        etf = b[b["symbol"].isin(ETFS)]
        hour = etf["time"].str[:2]
        total = etf["amount"].sum() / 1e8
        late = etf.loc[etf["time"] >= "14:00", "amount"].sum() / 1e8
        ix = b[b["symbol"] == "sh000300"].sort_values("time")
        if ix.empty or not (ix["time"] < "14:00").any() or total == 0:
            continue  # partial sessions (the backfill kept only part of some early days)
        r_day = ix["close"].iloc[-1] / ix["open"].iloc[0] - 1
        r_late = ix["close"].iloc[-1] / ix.loc[ix["time"] < "14:00", "close"].iloc[-1] - 1
        low_t = ix.loc[ix["close"].idxmin(), "time"]
        rows.append({"day": day.date(), "creation": d["creation"].get(day, np.nan), "etf_turnover": total,
                     "after_1400": late / total if total else np.nan, "index_open_close": r_day, "index_after_1400": r_late,
                     "index_low_at": low_t})
    if rows:
        t = pd.DataFrame(rows)
        print("\n10-08 以来每天（四只 ETF 合计；申购为当晚公布的数字）：")
        print(t.to_string(index=False, formatters={"creation": "{:+.1f}".format, "etf_turnover": "{:.1f}".format,
                                                   "after_1400": "{:.0%}".format, "index_open_close": "{:+.2%}".format,
                                                   "index_after_1400": "{:+.2%}".format}))


if __name__ == "__main__":
    main()
