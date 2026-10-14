"""Buying SSE ETF puts on every session: how cheap to buy, when to sell, which expiry, which strike, and how the
speed of the fall matters.  Follow-up of put_after_breakdown.py (question of 2026-10-11).

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/options/put_grid.py \
        ~/L1/research/options/data data/market.duckdb

Same conventions as put_after_breakdown.py: standard puts only, entry at the next session's close, expiry by the
fourth-Wednesday rule, returns on the premium.  Every session is an entry (trades overlap, so counts overstate
the independent evidence).  Groups:
  cheapness  the at-the-money near-month implied volatility's percentile among the previous 250 sessions
  expiry     sessions left at entry: 15-30, 31-60, 61-130 (nearest expiry inside the band)
  strike     delta closest to -0.75, -0.5, -0.25, -0.10 (within 0.12)
  exit       expiry; 20 sessions; +50% or +100% take-profit; implied volatility percentile >= 80%; index down 5%
             (the last four checked at each close, otherwise held to expiry; at-the-money near-month only)
  path       index change over a 20-session hold, and whether most of a fall came in the first 5 sessions
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from put_after_breakdown import fourth_wednesday, load

UNDERLYINGS = {"510050": ("sh000016", "50ETF"), "510300": ("sh000300", "300ETF"),
               "510500": ("sh000905", "500ETF"), "588000": ("sh000688", "科创50")}
TENORS = {"近月 15-30": (15, 30), "次月 31-60": (31, 60), "远月 61-130": (61, 130)}
DELTAS = {"实值 -0.75": -0.75, "平值 -0.50": -0.50, "虚值 -0.25": -0.25, "深度虚值 -0.10": -0.10}
TOL = 0.12
HOLD = 20
CHEAP, DEAR = 0.4, 0.6


def prepare(underlying, contracts, risk, sina, database, index_symbol):
    own = risk[risk["SECURITY_ID"].isin(contracts.loc[contracts["underlying"] == underlying, "SECURITY_ID"])]
    days = pd.DatetimeIndex(sorted(own["date"].unique()))
    pos = {d: i for i, d in enumerate(days)}
    standard = ~contracts["adjusted"].astype(bool)
    puts = contracts[(contracts["underlying"] == underlying) & (contracts["kind"] == "put") & standard]

    def session_on_or_after(d):
        i = days.searchsorted(pd.Timestamp(d))
        return days[i] if i < len(days) else None

    expiry = {m: session_on_or_after(fourth_wednesday(m)) for m in puts["expiry_month"].unique()}
    puts = puts.assign(expiry=puts["expiry_month"].map(expiry)).dropna(subset=["expiry"])
    cand = own.merge(puts[["SECURITY_ID", "expiry", "strike"]], on="SECURITY_ID")
    cand = cand[cand["DELTA_VALUE"] < 0].copy()
    cand["left"] = cand["expiry"].map(pos) - cand["date"].map(pos)
    atm = pick(cand, 15, 10**6, -0.5)
    iv = atm["IMPLC_VOLATLTY"].where(atm["IMPLC_VOLATLTY"] > 0).reindex(days)
    iv_pct = iv.rolling(250, min_periods=120).apply(
        lambda w: np.mean(w[:-1][~np.isnan(w[:-1])] < w[-1]) if not np.isnan(w[-1]) else np.nan, raw=True)
    with duckdb.connect(str(database), read_only=True) as con:
        bars = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY 1",
                           [index_symbol]).fetchdf()
    index = bars.set_index(pd.to_datetime(bars["trade_date"]))["close"].astype(float).reindex(days).ffill()
    price = sina.drop_duplicates(["SECURITY_ID", "date"]).set_index(["SECURITY_ID", "date"])["close"]
    return {"days": days, "pos": pos, "cand": cand, "iv": iv, "iv_pct": iv_pct, "index": index, "price": price}


def pick(cand, lo, hi, target):
    c = cand[(cand["left"] >= lo) & (cand["left"] <= hi)]
    c = c[c["expiry"] == c.groupby("date")["expiry"].transform("min")]
    c = c.assign(dist=(c["DELTA_VALUE"] - target).abs())
    c = c[c["dist"] <= TOL]
    return c.sort_values(["date", "dist"]).drop_duplicates("date").set_index("date")


def build(ctx, chosen, path_rules=False):
    days, pos, price, index, iv_pct = ctx["days"], ctx["pos"], ctx["price"], ctx["index"], ctx["iv_pct"]
    last = len(days) - 1
    rows = []
    for t, row in chosen.iterrows():
        i = pos[t] + 1
        if i > last:
            continue
        sid, exp_i = row["SECURITY_ID"], pos.get(row["expiry"])
        entry = price.get((sid, days[i]), np.nan)
        if exp_i is None or not entry == entry or entry <= 0:
            continue
        rec = {"date": t, "iv": ctx["iv"].get(t), "iv_pct": iv_pct.get(t), "entry": entry}
        p_exp = price.get((sid, days[exp_i]), np.nan) if exp_i <= last else np.nan
        rec["r_expiry"] = p_exp / entry - 1
        j = min(i + HOLD, exp_i)
        rec["r_hold"] = price.get((sid, days[j]), np.nan) / entry - 1 if j <= last else np.nan
        if j <= last:
            rec["idx_hold"] = index.iloc[j] / index.iloc[i] - 1
            rec["idx_first5"] = index.iloc[min(i + 5, j)] / index.iloc[i] - 1
        if path_rules and exp_i <= last:
            exits = {"tp50": None, "tp100": None, "iv80": None, "idx5": None}
            for k in range(i + 1, exp_i + 1):
                p = price.get((sid, days[k]), np.nan)
                if p != p:
                    continue
                if exits["tp50"] is None and p >= entry * 1.5:
                    exits["tp50"] = p
                if exits["tp100"] is None and p >= entry * 2.0:
                    exits["tp100"] = p
                if exits["iv80"] is None and iv_pct.iloc[k] >= 0.8:
                    exits["iv80"] = p
                if exits["idx5"] is None and index.iloc[k] <= index.iloc[i] * 0.95:
                    exits["idx5"] = p
            for name, p in exits.items():
                rec[f"r_{name}"] = (p if p is not None else p_exp) / entry - 1
        rows.append(rec)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if r.empty:
        return {"次数": 0}
    return {"次数": len(r), "平均": f"{r.mean():+.0%}", "中位数": f"{r.median():+.0%}", "赚钱": f"{(r > 0).mean():.0%}",
            "翻倍以上": f"{(r >= 1).mean():.0%}", "亏九成以上": f"{(r <= -0.9).mean():.0%}"}


def main() -> None:
    data, database = Path(sys.argv[1]).expanduser(), Path(sys.argv[2])
    contracts, risk, sina = load(data)
    for underlying, (index_symbol, name) in UNDERLYINGS.items():
        ctx = prepare(underlying, contracts, risk, sina, database, index_symbol)
        base = build(ctx, pick(ctx["cand"], 15, 30, -0.5), path_rules=True)
        print(f"\n################ {name}：{base.index[0].date()} .. {base.index[-1].date()}，平值近月 {len(base)} 天")

        # A. how cheap: percentile buckets and the implied volatility levels behind them
        base["档"] = pd.cut(base["iv_pct"], [-0.01, 0.2, 0.4, 0.6, 0.8, 1.0],
                           labels=["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"])
        rows = []
        for k, g in base.groupby("档", observed=True):
            rows.append({"分位": k, "隐含波动率（10%–90% 区间）": f"{g['iv'].quantile(.1):.0%}–{g['iv'].quantile(.9):.0%}",
                         **stats(g["r_expiry"]), "指数（持有期）": f"{g['idx_hold'].mean():+.1%}"})
        print("\n== A. 期权便宜程度（平值近月，持有到期）")
        print(pd.DataFrame(rows).to_string(index=False))
        yearly = base.assign(year=base.index.year).groupby("year").apply(
            lambda g: pd.Series({"便宜（≤40%）": g.loc[g["iv_pct"] <= CHEAP, "r_expiry"].mean(),
                                 "贵（≥60%）": g.loc[g["iv_pct"] >= DEAR, "r_expiry"].mean()}))
        yearly["便宜更好"] = yearly["便宜（≤40%）"] > yearly["贵（≥60%）"]
        both = yearly.dropna(subset=["便宜（≤40%）", "贵（≥60%）"])
        print(f"   按年：两组都有交易的 {len(both)} 年里，便宜组更好的有 {int(both['便宜更好'].sum())} 年")
        print(yearly.to_string(formatters={"便宜（≤40%）": "{:+.0%}".format, "贵（≥60%）": "{:+.0%}".format}))

        # B. expiry, C. strike
        print("\n== B. 期限（平值）：持有 20 日 / 持有到期；全部 和 只在便宜时（≤40%）")
        rows = []
        for tenor, (lo, hi) in TENORS.items():
            tr = build(ctx, pick(ctx["cand"], lo, hi, -0.5))
            if tr.empty:
                continue
            cheap = tr[tr["iv_pct"] <= CHEAP]
            for label, frame in (("全部", tr), ("便宜时", cheap)):
                rows.append({"期限": tenor, "买入时机": label, "持有20日": stats(frame["r_hold"]).get("平均"),
                             "20日赚钱": stats(frame["r_hold"]).get("赚钱"), "到期": stats(frame["r_expiry"]).get("平均"),
                             "到期赚钱": stats(frame["r_expiry"]).get("赚钱"), "次数": len(frame)})
        print(pd.DataFrame(rows).to_string(index=False))
        print("\n== C. 行权价（近月）：持有到期")
        rows = []
        for label, target in DELTAS.items():
            tr = build(ctx, pick(ctx["cand"], 15, 30, target))
            if tr.empty:
                continue
            for when, frame in (("全部", tr), ("便宜时", tr[tr["iv_pct"] <= CHEAP])):
                rows.append({"行权价": label, "买入时机": when, "平均权利金": f"{frame['entry'].mean():.4f}",
                             **stats(frame["r_expiry"])})
        print(pd.DataFrame(rows).to_string(index=False))

        # D. exit rules
        print("\n== D. 卖出方式（平值近月）")
        rows = []
        for label, col in (("持有到期", "r_expiry"), ("持有 20 日", "r_hold"), ("赚 50% 就卖", "r_tp50"),
                           ("赚 100% 就卖", "r_tp100"), ("期权变贵（分位≥80%）就卖", "r_iv80"), ("指数跌 5% 就卖", "r_idx5")):
            for when, frame in (("全部", base), ("便宜时", base[base["iv_pct"] <= CHEAP])):
                rows.append({"卖出方式": label, "买入时机": when, **stats(frame[col])})
        print(pd.DataFrame(rows).to_string(index=False))

        # E. speed of the fall
        print("\n== E. 下跌快慢（平值近月，持有 20 日）")
        h = base.dropna(subset=["r_hold", "idx_hold"]).copy()
        h["指数20日"] = pd.cut(h["idx_hold"], [-1, -0.06, -0.03, 0, 0.03, 1],
                              labels=["跌超6%", "跌3-6%", "跌0-3%", "涨0-3%", "涨超3%"])
        fall = h["idx_hold"] < 0
        h["节奏"] = np.where(~fall, "—", np.where(h["idx_first5"] <= h["idx_hold"] * 2 / 3, "急跌（前5天跌了三分之二以上）",
                                                 "缓跌"))
        rows = []
        for (bucket, pace), g in h.groupby(["指数20日", "节奏"], observed=True):
            rows.append({"指数20日": bucket, "节奏": pace, **stats(g["r_hold"]),
                         "指数平均": f"{g['idx_hold'].mean():+.1%}"})
        print(pd.DataFrame(rows).to_string(index=False))
        fell = h[h["idx_hold"] < 0]
        print(f"   指数在持有期下跌的 {len(fell)} 次里，认沽仍然亏钱的占 {(fell['r_hold'] <= 0).mean():.0%}")


if __name__ == "__main__":
    main()
