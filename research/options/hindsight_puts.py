"""Hindsight: the puts and calls bought at the close of a recent high, valued at the latest close.  Question of
2026-10-18 (the decline since 2026-09-22).

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/options/hindsight_puts.py data/market.duckdb \
        2026-09-22 2026-10-16

Every standard contract (adjusted ones skipped) with a close on the entry day and at least 3 days to expiry.  Exit:
the latest close up to the exit day, or the expiry-day close.  Also the best close in between (hindsight twice).
Costs per contract and side: SSE 2.5 yuan fee + 1 tick (1 yuan); CFFEX 15 yuan fee + 1 tick (0.2 point = 20 yuan);
these are assumptions.  Moneyness by the entry-day delta (SSE: exchange risk indicators; CFFEX: the exchange's
daily statistics).
"""

from __future__ import annotations

import sys
from datetime import date

import duckdb
import numpy as np
import pandas as pd

SPOT = {"510050": ("etf_bars", "510050", "50ETF"), "510300": ("etf_bars", "510300", "300ETF"),
        "510500": ("etf_bars", "510500", "500ETF"), "588000": ("etf_bars", "588000", "科创50 华夏"),
        "588080": ("etf_bars", "588080", "科创50 易方达"), "IO": ("index_bars", "sh000300", "IO 沪深300"),
        "MO": ("index_bars", "sh000852", "MO 中证1000"), "HO": ("index_bars", "sh000016", "HO 上证50")}
COST = {"SSE": 3.5, "CFFEX": 35.0}
UNIT = {"SSE": 10_000, "CFFEX": 100}
BUCKETS = [(0.8, 1.01, "深度实值"), (0.6, 0.8, "实值"), (0.4, 0.6, "平值"), (0.2, 0.4, "虚值"), (0.05, 0.2, "深度虚值"),
           (0.0, 0.05, "极度虚值")]


def bucket(delta: float) -> str | None:
    if delta != delta:
        return None
    d = abs(delta)
    return next((name for lo, hi, name in BUCKETS if lo <= d < hi), None)


def main() -> None:
    database, entry, exit_ = sys.argv[1], date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3])
    con = duckdb.connect(database, read_only=True)
    terms = con.execute("SELECT contract, exchange, underlying, kind, expiry_month, expiry_date, strike FROM "
                        "option_contracts WHERE NOT adjusted").fetchdf()
    terms["expiry_date"] = pd.to_datetime(terms["expiry_date"]).dt.date
    bars = con.execute("SELECT contract, trade_date, close, exchange_delta, volume FROM option_daily_bars "
                       "WHERE trade_date BETWEEN ? AND ?", [entry, exit_]).fetchdf()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    risk = con.execute("SELECT contract, delta, iv FROM option_risk WHERE trade_date = ?", [entry]).fetchdf()
    spots = {}
    for u, (table, symbol, _) in SPOT.items():
        s = con.execute(f"SELECT trade_date, close FROM {table} WHERE symbol = ? AND trade_date BETWEEN ? AND ? "
                        "ORDER BY 1", [symbol, entry, exit_]).fetchdf()
        spots[u] = pd.Series(s["close"].astype(float).to_numpy(), index=pd.to_datetime(s["trade_date"]).dt.date)

    print(f"entry {entry} close, exit {exit_} close (or expiry)")
    print("\n== 标的涨跌")
    for u, s in spots.items():
        print(f"{SPOT[u][2]:>10}: {s.iloc[0]:.3f} → {s.iloc[-1]:.3f}，{s.iloc[-1] / s.iloc[0] - 1:+.1%}；期间最低 "
              f"{s.min():.3f}（{s.idxmin()}，{s.min() / s.iloc[0] - 1:+.1%}）")

    first = bars[(bars["trade_date"] == entry) & (bars["close"] > 0)].merge(terms, on="contract")
    first = first[[(e - entry).days >= 3 for e in first["expiry_date"]]]
    first = first.merge(risk, on="contract", how="left")
    first["delta"] = first["delta"].fillna(first["exchange_delta"])
    rows = []
    for r in first.itertuples(index=False):
        later = bars[(bars["contract"] == r.contract) & (bars["trade_date"] > entry)
                     & (bars["trade_date"] <= min(exit_, r.expiry_date))].sort_values("trade_date")
        if later.empty:
            continue
        unit, cost = UNIT[r.exchange], COST[r.exchange]
        out = later["close"].iloc[-1]
        best = later.loc[later["close"].idxmax()]
        rows.append({"underlying": r.underlying, "kind": r.kind, "contract": r.contract, "month": r.expiry_month,
                     "expiry": r.expiry_date,
                     "strike": r.strike, "spot": spots[r.underlying].iloc[0], "delta": r.delta, "iv": r.iv,
                     "bucket": bucket(r.delta), "entry_volume": r.volume, "entry": r.close, "exit": out,
                     "exit_day": later["trade_date"].iloc[-1], "best": best["close"], "best_day": best["trade_date"],
                     "pnl": (out - r.close) * unit - 2 * cost, "outlay": r.close * unit + cost,
                     "best_pnl": (best["close"] - r.close) * unit - 2 * cost})
    t = pd.DataFrame(rows)
    t["ret"] = t["pnl"] / t["outlay"]
    t["best_ret"] = t["best_pnl"] / t["outlay"]
    t["gross"] = t["exit"] / t["entry"] - 1
    t["tenor"] = [f"{m}（{(e - entry).days}天）" for m, e in zip(t["month"], t["expiry"])]
    t.to_csv(f"hindsight_{entry}.csv", index=False, encoding="utf-8-sig")
    order = [b[2] for b in BUCKETS]

    for kind, label in (("put", "认沽"), ("call", "认购")):
        k = t[t["kind"] == kind]
        print(f"\n== 买入{label}：扣成本后收益的中位数（按到期月 × 价内外，括号里是合约数）")
        for u in SPOT:
            g = k[k["underlying"] == u]
            if g.empty:
                continue
            grid = g.groupby(["tenor", "bucket"])["ret"].agg(["median", "size"])
            tenors = sorted(g["tenor"].unique())
            table = pd.DataFrame({b: [f"{grid.loc[(m, b), 'median']:+.0%}({int(grid.loc[(m, b), 'size'])})"
                                      if (m, b) in grid.index else "" for m in tenors] for b in order}, index=tenors)
            print(f"\n{SPOT[u][2]}")
            print(table.to_string())

    puts = t[(t["kind"] == "put")]
    print("\n== 认沽：每个标的收益最高的 3 个（入场价至少 50 元/张，避免几块钱的合约）")
    show = ["contract", "month", "strike", "spot", "delta", "iv", "entry", "exit", "ret", "pnl", "best", "best_day",
            "best_ret", "entry_volume"]
    for u in SPOT:
        g = puts[(puts["underlying"] == u) & (puts["entry"] * UNIT["CFFEX" if u in ("IO", "MO", "HO") else "SSE"] >= 50)]
        if g.empty:
            continue
        print(f"\n{SPOT[u][2]}")
        print(g.sort_values("ret", ascending=False).head(3)[show].to_string(
            index=False, formatters={"ret": "{:+.0%}".format, "best_ret": "{:+.0%}".format, "delta": "{:.2f}".format,
                                     "iv": lambda v: "" if v != v else f"{v:.0%}", "pnl": "{:,.0f}".format}))

    print("\n== 平值认沽（delta 最接近 -0.5）各期限：一张赚多少")
    for u in SPOT:
        g = puts[puts["underlying"] == u]
        for tenor, h in g.groupby("tenor"):
            r = h.iloc[(h["delta"] + 0.5).abs().argsort().iloc[0]]
            print(f"{SPOT[u][2]:>10} {tenor} 行权价 {r['strike']}: {r['entry']} → {r['exit']}，"
                  f"一张 {r['pnl']:+,.0f} 元（{r['ret']:+.0%}），最好时 {r['best_ret']:+.0%}（{r['best_day']}）")


if __name__ == "__main__":
    main()
