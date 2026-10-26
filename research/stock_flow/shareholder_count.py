"""股东户数 (number of shareholders): does a falling count — chips concentrating, the folk sign of 吸筹 — lead to better
returns?  Question of 2026-10-25.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/shareholder_count.py data/market.duckdb \
        ~/L1/research/stock_flow/data

Counts: cninfo (AKShare stock_hold_num_cninfo, one request per report period, cached), quarter ends 2016Q1–2026Q2.
A count is known from the notice date of that period's report (fin_income.notice_date, first version); the entry is
the next session's open.  Rules fixed beforehand: each period, the stocks with a 20-session average turnover of 30
million yuan or more at entry, in quintiles of the count's change (股东人数增幅) and, separately, within terciles of
the 60-session return before the entry (the count follows the price: rising stocks attract holders).  Outcome: the
excess over the universe's mean of the close 20, 60 and 120 sessions after the entry.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak
import duckdb
import numpy as np
import pandas as pd

PERIODS = [f"{y}{md}" for y in range(2016, 2027) for md in ("0331", "0630", "0930", "1231")][:42]  # to 2026Q2


def counts(data: Path) -> pd.DataFrame:
    folder = data / "shareholder_counts"
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for period in PERIODS:
        path = folder / f"{period}.parquet"
        if not path.exists():
            raw = ak.stock_hold_num_cninfo(date=period)
            raw.to_parquet(path, index=False)
            time.sleep(3)
        raw = pd.read_parquet(path)
        frames.append(pd.DataFrame({"symbol": raw["证券代码"].astype(str).str.zfill(6),
                                    "report_date": pd.to_datetime(raw["变动日期"]),
                                    "holders": pd.to_numeric(raw["本期股东人数"], errors="coerce"),
                                    "change": pd.to_numeric(raw["股东人数增幅"], errors="coerce") / 100}))
    out = pd.concat(frames, ignore_index=True)
    return out[out["report_date"].dt.strftime("%m%d").isin(["0331", "0630", "0930", "1231"])].drop_duplicates(
        ["symbol", "report_date"])


def main() -> None:
    database, data = sys.argv[1], Path(sys.argv[2]).expanduser()
    hc = counts(data)
    con = duckdb.connect(database, read_only=True)
    notices = con.execute("SELECT symbol, report_date, min(notice_date) AS notice_date FROM fin_income "
                          "GROUP BY 1, 2").fetchdf()
    notices["report_date"] = pd.to_datetime(notices["report_date"])
    notices["notice_date"] = pd.to_datetime(notices["notice_date"])
    ev = hc.merge(notices, on=["symbol", "report_date"], how="inner").dropna(subset=["change", "notice_date"])
    bars = con.execute("SELECT symbol, trade_date, hfq_open AS o, hfq_close AS c, turnover_cny AS amt "
                       "FROM daily_bars_adjusted WHERE trade_date >= DATE '2015-10-01' AND hfq_close > 0").fetchdf()
    o = bars.pivot(index="trade_date", columns="symbol", values="o")
    c = bars.pivot(index="trade_date", columns="symbol", values="c")
    amt = bars.pivot(index="trade_date", columns="symbol", values="amt")
    dates = pd.DatetimeIndex(c.index)
    universe = amt.rolling(20, min_periods=15).mean() >= 3e7
    r60 = c / c.shift(60) - 1
    excess = {}
    for n in (20, 60, 120):
        f = c.shift(-(n - 1)) / o - 1  # from the entry day's open to the close n sessions later
        excess[n] = f.sub(f.where(universe).mean(axis=1), axis=0)
    rows = []
    cols = {s: k for k, s in enumerate(c.columns)}
    for r in ev.itertuples(index=False):
        i = dates.searchsorted(r.notice_date, side="right")  # the first session after the notice
        j = cols.get(r.symbol)
        if j is None or i >= len(dates) or not universe.iat[i, j]:
            continue
        rows.append({"symbol": r.symbol, "report_date": r.report_date, "entry": dates[i], "change": r.change,
                     "r60": r60.iat[i - 1, j] if i > 0 else np.nan,
                     **{f"x{n}": excess[n].iat[i, j] for n in (20, 60, 120)}})
    t = pd.DataFrame(rows).dropna(subset=["change", "x20"])
    t["q"] = t.groupby("report_date")["change"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)) + 1
    print(f"{len(t):,} 个股票季度，{t['report_date'].nunique()} 个报告期（{t['report_date'].min():%Y-%m}–"
          f"{t['report_date'].max():%Y-%m}）")
    g = t.groupby("q").agg(股东人数增幅中位数=("change", "median"), 次数=("x20", "size"), 前60日涨跌=("r60", "mean"),
                           超额20日=("x20", "mean"), 超额60日=("x60", "mean"), 超额120日=("x120", "mean"))
    g.index = ["1（户数减少最多）", "2", "3", "4", "5（户数增加最多）"]
    print(g.to_string(formatters={"股东人数增幅中位数": "{:+.1%}".format, "前60日涨跌": "{:+.1%}".format,
                                  "超额20日": "{:+.2%}".format, "超额60日": "{:+.2%}".format,
                                  "超额120日": "{:+.2%}".format}))
    t["r_t"] = t.groupby("report_date")["r60"].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=False))
    print("\n控制前 60 日涨跌后（每格 60 日超额）：户数减少最多一档 vs 增加最多一档")
    for k, name in ((0, "前期跌得多"), (1, "前期中间"), (2, "前期涨得多")):
        s = t[t["r_t"] == k]
        sq = s.groupby("report_date")["change"].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False))
        lo, hi = s[sq == 0]["x60"], s[sq == 4]["x60"]
        print(f"  {name}：户数减少最多 {lo.mean():+.2%}（{len(lo)}），增加最多 {hi.mean():+.2%}（{len(hi)}）")
    spread = t.groupby("report_date").apply(lambda s: s.loc[s["q"] == 1, "x60"].mean() - s.loc[s["q"] == 5, "x60"].mean())
    print(f"\n每个报告期“户数减少最多 − 增加最多”的 60 日超额差：平均 {spread.mean():+.2%}，为正的报告期 "
          f"{(spread > 0).mean():.0%}（{len(spread)} 期），t {spread.mean() / spread.std() * np.sqrt(len(spread)):.1f}")
    print(spread.groupby(spread.index.year).mean().to_string(float_format=lambda v: f"{v:+.2%}"))


if __name__ == "__main__":
    main()
