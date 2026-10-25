"""How US and A-share indices move together (question of 2026-10-24).

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/us_markets/us_ashare_relation.py \
        data/market.duckdb ~/L1/research/us_markets/data/us_indices.parquet

Timing: a US session (date d, New York) closes at 04:00/05:00 Beijing time on d+1, after the A-share session of d
and before the next one.  So "US -> A-share" pairs the US return of d with the first A-share session after d,
split into the opening gap (open / previous close) and the session (close / open); "A-share -> US" pairs the A-share
return of d with the US return of d (the A-share session ends before New York opens).  Log returns.
"""

from __future__ import annotations

import sys

import duckdb
import numpy as np
import pandas as pd

A = {"sh000300": "沪深300", "sh000001": "上证指数", "sh000016": "上证50", "sz399006": "创业板指", "sh000688": "科创50",
     "sh000852": "中证1000"}
US = {".INX": "标普500", ".NDX": "纳斯达克100", ".SOX": "费城半导体", ".HXC": "中国金龙"}
PERIODS = [("2005", "2009"), ("2010", "2014"), ("2015", "2019"), ("2020", "2022"), ("2023", "2026")]


def load(database: str, us_path: str):
    con = duckdb.connect(database, read_only=True)
    a = con.execute("SELECT symbol, trade_date, open, close FROM index_bars WHERE symbol IN ({}) ORDER BY 2".format(
        ",".join(f"'{s}'" for s in A))).fetchdf()
    a["trade_date"] = pd.to_datetime(a["trade_date"])
    us = pd.read_parquet(us_path)
    return a, us


def next_session_pairs(us_close: pd.Series, a: pd.DataFrame) -> pd.DataFrame:
    """US log return of day d with the first A-share session after d: total, gap and session returns."""
    a = a.sort_values("trade_date").copy()
    a["prev"] = a["close"].shift(1)
    a["total"] = np.log(a["close"] / a["prev"])
    a["gap"] = np.log(a["open"] / a["prev"])
    a["session"] = np.log(a["close"] / a["open"])
    us_ret = np.log(us_close / us_close.shift(1)).dropna()
    days = a["trade_date"].to_numpy()
    idx = np.searchsorted(days, us_ret.index.to_numpy(), side="right")
    ok = idx < len(a)
    nxt = a.iloc[idx[ok]].reset_index(drop=True)
    out = pd.DataFrame({"us_date": us_ret.index[ok], "us": us_ret.to_numpy()[ok], "a_date": nxt["trade_date"],
                        "total": nxt["total"], "gap": nxt["gap"], "session": nxt["session"]})
    # several US sessions before one A-share session (A-share holidays): keep the last one only
    return out.drop_duplicates("a_date", keep="last").dropna()


def main() -> None:
    a_all, us_all = load(sys.argv[1], sys.argv[2])
    a = {s: g.set_index("trade_date") for s, g in a_all.groupby("symbol")}
    us = {s: g.set_index("trade_date")["close"].astype(float) for s, g in us_all.groupby("symbol")}
    a_close = pd.DataFrame({A[s]: a[s]["close"] for s in A})
    us_close = pd.DataFrame({US[s]: us[s] for s in US})

    print("== 1. 美股当天 → A 股下一个交易日（日收益相关系数；全期 / 2023 年以来）")
    rows = []
    for us_sym, us_name in US.items():
        for a_sym, a_name in A.items():
            p = next_session_pairs(us[us_sym], a_all[a_all["symbol"] == a_sym])
            recent = p[p["us_date"] >= "2023-01-01"]
            rows.append({"美股": us_name, "A股": a_name, "起": p["us_date"].min().year,
                         "全天": p["us"].corr(p["total"]), "开盘跳空": p["us"].corr(p["gap"]),
                         "开盘后到收盘": p["us"].corr(p["session"]), "全天(2023起)": recent["us"].corr(recent["total"]),
                         "跳空(2023起)": recent["us"].corr(recent["gap"]),
                         "斜率(2023起)": np.polyfit(recent["us"], recent["total"], 1)[0]})
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print("\n== 2. 反方向：A 股当天 → 同一天稍晚的美股（日收益相关系数）")
    for a_name in ("沪深300", "上证指数"):
        ra = np.log(a_close[a_name] / a_close[a_name].shift(1))
        for us_name in ("标普500", "纳斯达克100"):
            ru = np.log(us_close[us_name] / us_close[us_name].shift(1))
            j = pd.concat([ra, ru], axis=1, join="inner").dropna()
            print(f"{a_name} → {us_name}: {j.iloc[:, 0].corr(j.iloc[:, 1]):.2f}（{j.index.min().year} 起）")

    print("\n== 3. 标普500 当天涨跌 → 沪深300 下一个交易日")
    p = next_session_pairs(us[".INX"], a_all[a_all["symbol"] == "sh000300"])
    p["bucket"] = pd.cut(np.expm1(p["us"]), [-1, -0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03, 1],
                         labels=["跌超3%", "跌2-3%", "跌1-2%", "跌0-1%", "涨0-1%", "涨1-2%", "涨2-3%", "涨超3%"])
    for label, frame in (("2005 年以来", p), ("2023 年以来", p[p["us_date"] >= "2023-01-01"])):
        g = frame.groupby("bucket", observed=True).agg(
            次数=("total", "size"), 沪深300平均=("total", lambda x: np.expm1(x).mean()),
            上涨比例=("total", lambda x: (x > 0).mean()), 开盘跳空平均=("gap", lambda x: np.expm1(x).mean()),
            开盘后平均=("session", lambda x: np.expm1(x).mean()))
        print(f"\n{label}（全部交易日：沪深300 平均 {np.expm1(frame['total']).mean():+.2%}，"
              f"上涨 {(frame['total'] > 0).mean():.0%}）")
        print(g.to_string(formatters={"沪深300平均": "{:+.2%}".format, "上涨比例": "{:.0%}".format,
                                      "开盘跳空平均": "{:+.2%}".format, "开盘后平均": "{:+.2%}".format}))

    print("\n== 4. 周、月收益的相关系数（沪深300 vs 标普500），分时期")
    weekly = np.log(pd.concat([a_close["沪深300"], us_close["标普500"]], axis=1).resample("W-FRI").last()).diff()
    monthly = np.log(pd.concat([a_close["沪深300"], us_close["标普500"]], axis=1).resample("ME").last()).diff()
    for lo, hi in PERIODS:
        w, m = weekly.loc[lo:hi].dropna(), monthly.loc[lo:hi].dropna()
        print(f"{lo}–{hi}: 周 {w.iloc[:, 0].corr(w.iloc[:, 1]):.2f}，月 {m.iloc[:, 0].corr(m.iloc[:, 1]):.2f}")
    for name in ("纳斯达克100", "中国金龙"):
        w = np.log(pd.concat([a_close["沪深300"], us_close[name]], axis=1).resample("W-FRI").last()).diff().dropna()
        print(f"沪深300 vs {name} 周收益相关（{w.index.min().year} 起）: {w.iloc[:, 0].corr(w.iloc[:, 1]):.2f}")
    w = np.log(pd.concat([a_close["科创50"], us_close["费城半导体"]], axis=1).resample("W-FRI").last()).diff().dropna()
    print(f"科创50 vs 费城半导体 周收益相关（{w.index.min().year} 起）: {w.iloc[:, 0].corr(w.iloc[:, 1]):.2f}")

    print("\n== 5. 每年涨跌")
    yearly = pd.DataFrame({n: s.resample("YE").last().pct_change() for n, s in
                           {**{k: a_close[k] for k in ("沪深300", "上证指数", "创业板指")},
                            **{k: us_close[k] for k in ("标普500", "纳斯达克100")}}.items()})
    yearly.index = yearly.index.year
    print(yearly.loc[2005:].to_string(float_format=lambda v: f"{v:+.0%}", na_rep=""))

    print("\n== 6. 大危机里的最大回撤（期间内从高点到低点）")
    for label, lo, hi in (("2008 金融危机", "2007-10-01", "2009-03-31"), ("2015 A 股股灾", "2015-06-01", "2016-02-29"),
                          ("2018 贸易摩擦", "2018-01-01", "2018-12-31"), ("2020 新冠", "2020-01-15", "2020-04-30"),
                          ("2022 加息", "2022-01-01", "2022-10-31"), ("2025-04 关税冲击", "2025-03-20", "2025-04-30"),
                          ("2026-08 起", "2026-08-01", "2026-10-23")):
        out = []
        for n, s in (("沪深300", a_close["沪深300"]), ("标普500", us_close["标普500"]),
                     ("纳斯达克100", us_close["纳斯达克100"])):
            x = s.loc[lo:hi].dropna()
            out.append(f"{n} {(x / x.cummax() - 1).min():+.0%}" if len(x) else f"{n} —")
        print(f"{label}: " + "，".join(out))

    print("\n== 7. 最近三个月")
    for frame, names in ((a_close, ("沪深300", "上证指数", "创业板指", "科创50", "中证1000")),
                         (us_close, ("标普500", "纳斯达克100", "费城半导体", "中国金龙"))):
        for n in names:
            s = frame[n].dropna()
            base = s.loc[:s.index[-1] - pd.DateOffset(months=3)].iloc[-1]
            print(f"{n}: 三个月 {s.iloc[-1] / base - 1:+.1%}，距一年内高点 {s.iloc[-1] / s.iloc[-250:].max() - 1:+.1%}"
                  f"（截至 {s.index[-1].date()}）")


if __name__ == "__main__":
    main()
