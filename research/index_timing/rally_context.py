"""Run on the server: cd ~/L1/MinervaQuantSystem && .venv/bin/python <this file> 2026-11-04  (question of 2026-11-04).

Today's rally in context (read-only on the production catalog): index trend since the high, breadth of the day,
intraday path and big-money flow of the broad-index ETFs (the monitor's snapshots), recent ETF creations."""

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DAY = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2026-11-04")
root = Path("/home/van/L1/MinervaQuantSystem")
con = duckdb.connect(str(root / "data/market.duckdb"), read_only=True)
NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指", "sh000016": "上证50", "sh000300": "沪深300",
         "sh000905": "中证500", "sh000852": "中证1000", "sh000688": "科创50"}
ix = con.execute(f"SELECT symbol, trade_date, open, high, low, close, turnover_cny AS amount FROM index_bars WHERE symbol IN "
                 f"{tuple(NAMES)} AND trade_date >= DATE '2025-01-01' ORDER BY symbol, trade_date").fetchdf()
ix["trade_date"] = pd.to_datetime(ix["trade_date"])
rows = []
for sym, d in ix.groupby("symbol"):
    d = d.set_index("trade_date").sort_index().loc[:DAY]
    c = d["close"]
    today, prev = d.iloc[-1], c.iloc[-2]
    peak_date = c.loc["2026-06-01":].idxmax()
    low_date = c.loc[peak_date:].idxmin()
    amt = d["amount"]
    rows.append({
        "指数": NAMES[sym], "今日": today.close / prev - 1, "高开": today.open / prev - 1,
        "收在日内": (today.close - today.low) / (today.high - today.low),
        "成交/20日均": today.amount / amt.iloc[-21:-1].mean() if amt.notna().sum() > 21 else np.nan,
        "高点": f"{peak_date:%m-%d}", "高点以来": today.close / c[peak_date] - 1, "最低": f"{low_date:%m-%d}",
        "最大回撤": c[low_date] / c[peak_date] - 1, "低点以来": today.close / c[low_date] - 1,
        "MA20": today.close / c.iloc[-20:].mean() - 1, "MA60": today.close / c.iloc[-60:].mean() - 1,
        "MA120": today.close / c.iloc[-120:].mean() - 1, "MA250": today.close / c.iloc[-250:].mean() - 1})
t = pd.DataFrame(rows).set_index("指数").loc[list(NAMES.values())]
pct = "{:+.1%}".format
print(t.to_string(formatters={**{k: pct for k in ("今日", "高开", "高点以来", "最大回撤", "低点以来", "MA20", "MA60", "MA120",
                                                   "MA250")}, "收在日内": "{:.0%}".format, "成交/20日均": "{:.2f}".format}))

print("\n最近 15 个交易日涨跌：")
for sym in ("sh000300", "sz399006", "sh000688"):
    d = ix[ix.symbol == sym].set_index("trade_date").loc[:DAY].tail(16)
    r = d["close"].pct_change().dropna()
    print(f"{NAMES[sym]:<6}", " ".join(f"{i:%m%d}{v:+.1%}" for i, v in r.items()))

# breadth: today and the recent sessions (A shares with a bar)
b = con.execute("""
    SELECT trade_date, count(*) n, avg((pct_change > 0)::int) up, avg((pct_change >= 5)::int) up5,
           avg((pct_change <= -5)::int) down5, sum(turnover_cny) amount
    FROM daily_bars WHERE trade_date >= DATE '2026-09-01' AND pct_change IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchdf()
b["amount_vs20"] = b["amount"] / b["amount"].rolling(20).mean().shift(1)
print("\n市场宽度（全部 A 股）：")
print(b.tail(10).to_string(index=False, formatters={"up": "{:.0%}".format, "up5": "{:.0%}".format,
                                                    "down5": "{:.0%}".format, "amount": lambda v: f"{v / 1e8:,.0f}亿",
                                                    "amount_vs20": "{:.2f}".format}))
lim = con.execute(f"""
    SELECT sum((b.close >= round(p.close * (1 + CASE WHEN s.board IN ('CHINEXT','STAR') THEN 0.2 WHEN s.board = 'BSE' THEN 0.3 ELSE 0.1 END), 2) - 0.001)::int) lu,
           sum((b.close <= round(p.close * (1 - CASE WHEN s.board IN ('CHINEXT','STAR') THEN 0.2 WHEN s.board = 'BSE' THEN 0.3 ELSE 0.1 END), 2) + 0.001)::int) ld
    FROM daily_bars b JOIN security_master s USING (symbol)
    JOIN daily_bars p ON p.symbol = b.symbol AND p.trade_date = (SELECT max(trade_date) FROM daily_bars WHERE trade_date < DATE '{DAY:%Y-%m-%d}')
    WHERE b.trade_date = DATE '{DAY:%Y-%m-%d}'""").fetchone()
print(f"今日约涨停 {lim[0]} 家、跌停 {lim[1]} 家（按昨收和板块涨跌幅限制估算，不含 ST 的 5%）")

# industries today (SW L1)
ind = con.execute(f"""
    SELECT i.l1_name, count(*) n, median(b.pct_change) med, avg((b.pct_change > 0)::int) up
    FROM daily_bars b JOIN industry_sw i ON i.symbol = b.symbol AND i.end_date IS NULL
    WHERE b.trade_date = DATE '{DAY:%Y-%m-%d}' GROUP BY 1 ORDER BY med DESC""").fetchdf()
print("\n申万一级行业（个股涨幅中位数）：前 6：", "，".join(f"{r.l1_name} {r.med:+.1f}%" for r in ind.head(6).itertuples()))
print("后 6：", "，".join(f"{r.l1_name} {r.med:+.1f}%" for r in ind.tail(6).itertuples()))

# intraday: the monitor's per-minute snapshots
out = root / "data/monitor/big_money" / f"{DAY:%Y-%m-%d}"
s = pd.read_parquet(out / "snapshots.parquet")
status = json.loads((out / "status.json").read_text(encoding="utf-8"))
avg = {g["id"]: g["avg_turnover"] for g in status["groups"]}
print("\n盘中报警：", status["alerts"])
marks = ["09:35", "10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
for g, d in s.groupby("group"):
    d = d.set_index("time")
    line = []
    for m in marks:
        if m in d.index:
            r = d.loc[m]
            line.append(f"{m} 指{r.index_change:+.1%} 倍{r.ratio:.1f} 净{r.big_net / 1e8:+.0f}")
    print(f"{g:<8}", " | ".join(line))
last = s[s.time == s.time.max()].set_index("group")
print("\n收盘：", "；".join(f"{g} 成交是平常 {r.ratio:.1f} 倍，大单净 {r.big_net / 1e8:+.1f} 亿（占 {r.big_share:.0%}），"
                         f"门槛 {max(2e8, 0.15 * avg[g]) / 1e8:.0f} 亿" for g, r in last.iterrows()))

# ETF creations of the recent sessions (published the next morning; today's is not in yet)
groups = {g["id"]: [f["symbol"] for f in g["funds"]] for g in status["groups"]}
sh = con.execute("SELECT s.symbol, s.trade_date, s.shares, b.close FROM etf_shares s JOIN etf_bars b "
                 "ON b.symbol = s.symbol AND b.trade_date = s.trade_date WHERE s.trade_date >= DATE '2026-10-15'").fetchdf()
sh = sh.sort_values(["symbol", "trade_date"])
sh["flow"] = sh.groupby("symbol")["shares"].diff() * sh["close"] / 1e8
print("\n监测的宽基 ETF 每日净申购（亿元，份额次日才公布）：")
rows = {}
for g, syms in groups.items():
    rows[g] = sh[sh.symbol.isin(syms)].groupby("trade_date")["flow"].sum()
print(pd.DataFrame(rows).round(0).tail(10).to_string())
