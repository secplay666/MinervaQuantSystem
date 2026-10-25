"""Do Chinese government bonds lead A-shares?  Check of a live-stream claim (2026-10-25): "30-year treasury
futures are the state's wallet: when they rise stocks fall, and when they hit resistance stocks bottom".

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/macro/bonds_stocks.py data/market.duckdb \
        ~/L1/research/macro/data/us_cn_rates.parquet

The 30-year yield (Eastmoney, bond_zh_us_rate) stands in for the 30-year treasury futures: the price moves
opposite to the yield.  Weekly (Friday) changes of the yield in basis points against CSI 300 returns.
Rules fixed beforehand: contemporaneous and lead/lag correlations; and a "bond turns down" signal (the 30-year
yield 10 bp or more above its 60-session low, first day after 20 sessions without a signal) followed by the CSI
300 over the next 20 and 60 sessions, against all days.
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd


def main() -> None:
    con = duckdb.connect(sys.argv[1], read_only=True)
    bars = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY 1").fetchdf()
    hs = pd.Series(bars["close"].astype(float).to_numpy(), index=pd.to_datetime(bars["trade_date"]))
    rates = pd.read_parquet(sys.argv[2])
    rates["日期"] = pd.to_datetime(rates["日期"])
    y30 = rates.set_index("日期")["中国国债收益率30年"].astype(float)
    y30 = y30[(y30 > 0.5) & (y30 < 8)].dropna()
    y10 = rates.set_index("日期")["中国国债收益率10年"].astype(float).dropna()
    print(f"30年收益率 {y30.index.min().date()}–{y30.index.max().date()}，最新 {y30.iloc[-1]:.3f}%；"
          f"10年 {y10.iloc[-1]:.3f}%")

    for label, y in (("30年", y30), ("10年", y10)):
        w = pd.concat([y.resample("W-FRI").last().diff() * 100, np.log(hs.resample("W-FRI").last()).diff()],
                      axis=1, keys=["dy", "r"]).dropna()
        out = []
        for lo, hi in (("2005", "2014"), ("2015", "2020"), ("2021", "2026")):
            p = w.loc[lo:hi]
            out.append(f"{lo}–{hi} 同周 {p['dy'].corr(p['r']):+.2f}，收益率领先一周 {p['dy'].shift(1).corr(p['r']):+.2f}，"
                       f"领先四周（四周变化→下四周） "
                       f"{p['dy'].rolling(4).sum().shift(4).corr(p['r'].rolling(4).sum()):+.2f}")
        print(f"\n{label}收益率周变化 vs 沪深300 周涨跌（正数 = 股债跷跷板：债跌（收益率升）时股涨）")
        print("\n".join(out))

    # signal: bonds turning down (yield up 10 bp from its 60-session low)
    y = y30.reindex(hs.index).ffill()
    low60 = y.rolling(60, min_periods=60).min()
    raw_signal = (y - low60) >= 0.10
    signal, last = [], -10**9
    for i, flag in enumerate(raw_signal.to_numpy()):
        if flag and not raw_signal.iloc[i - 1] and i - last > 20:
            signal.append(hs.index[i])
            last = i
    fwd20 = hs.shift(-20) / hs - 1
    fwd60 = hs.shift(-60) / hs - 1
    s = pd.DataFrame({"fwd20": fwd20.reindex(signal), "fwd60": fwd60.reindex(signal)})
    base = pd.DataFrame({"fwd20": fwd20, "fwd60": fwd60}).loc[y30.index.min():]
    print(f"\n“国债见顶回落”信号（30年收益率比 60 日低点高 10 个基点以上）：{len(s)} 次")
    for col, n in (("fwd20", 20), ("fwd60", 60)):
        x = s[col].dropna()
        print(f"之后 {n} 个交易日沪深300：平均 {x.mean():+.1%}，上涨 {(x > 0).mean():.0%}（{len(x)} 次）；"
              f"所有交易日平均 {base[col].mean():+.1%}，上涨 {(base[col].dropna() > 0).mean():.0%}")
    print("最近 8 次：" + "；".join(f"{d.date()} 之后20日 {r.fwd20:+.1%}" for d, r in s.tail(8).iterrows()))

    print("\n2026 年逐月：30年收益率（月末）、沪深300 月涨跌")
    m = pd.concat([y30.resample("ME").last(), hs.resample("ME").last().pct_change()], axis=1, keys=["y30", "hs300"])
    print(m.loc["2025-10":].to_string(formatters={"y30": "{:.3f}".format, "hs300": "{:+.1%}".format}))
    print("\n2026-07 中旬到 8 月：")
    print(pd.concat([y30, hs], axis=1, keys=["y30", "hs300"]).loc["2026-07-14":"2026-08-07"].dropna()
          .to_string(formatters={"y30": "{:.3f}".format, "hs300": "{:.0f}".format}))


if __name__ == "__main__":
    main()
