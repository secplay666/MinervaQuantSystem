"""Can the short-term direction of an A-share index be predicted?  Volume-price, flows, sentiment and valuation
signals tested on CSI 300 and ChiNext (question of 2026-10-25, after the 10-24 live stream).

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/index_timing/index_signals.py data/market.duckdb \
        ~/L1/research/index_timing/data ~/L1/research/macro/data/us_cn_rates.parquet

Rules, fixed before looking at the results.  Forward returns from the signal day's close over 5, 20 and 60
sessions.  Events count once per 20 sessions (cool-down).
  E1 跌久放巨量    drawdown from the 250-session high <= -20%, 60-session return <= -10%, and the day's turnover >= 2x
                  its 60-session average (the stream's "after a long fall, big money comes in -> rebound")
  E1b (added after the first run, labelled as such) the same with a 15% drawdown, -8% and 1.5x turnover
  E2 大资金申购    5-session net creation, known one session later (shares are published after the close), of the broad ETFs (yuan: share change x close) above the 98th percentile of
                  all earlier 5-session windows (from 2016-10, when both exchanges' shares exist)
  E3 E2 during a drawdown of 15% or more
  E4 短期超跌      5-session return <= -7% (CSI 300) / -9% (ChiNext)
  Continuous, month ends (non-overlapping months), quintiles of the predictor known at that close:
  P1 120-session return (time-series momentum) -> next 20 sessions
  P2 turnover heat: 20-session average turnover / 250-session average -> next 60
  P3 market congestion (legulegu: turnover share of the most active 5% of stocks) -> next 60
  P4 股债性价比: 1 / CSI 300 trailing PE (legulegu) - 10-year CGB yield -> next 60 and 250
  P5 SSE margin balance, 20-session change (from 2018-07) -> next 20
  Combined: logistic regression on P1, P2, P4, the 5- and 20-session returns, refitted each month end on all
  earlier month ends (from 2010), predicting the sign of the next 20 sessions; out-of-sample hit rate against
  "always up", and a switch strategy (in the index when the predicted probability > 0.5, else cash at 0).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak
import duckdb
import numpy as np
import pandas as pd

BROAD_ETFS = ["510300", "510310", "510330", "159919", "510050", "510500", "159922", "159915", "588000", "512100",
              "159845", "510180"]


def cached(path: Path, fetch):
    if not path.exists():
        fetch().to_parquet(path, index=False)
        time.sleep(1)
    return pd.read_parquet(path)


def load(database: str, data: Path, rates_path: str):
    data.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database, read_only=True)
    bars = con.execute("SELECT symbol, trade_date, close, turnover_cny FROM index_bars WHERE symbol IN "
                       "('sh000300', 'sz399006') ORDER BY 2").fetchdf()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    idx = {s: g.set_index("trade_date")[["close", "turnover_cny"]].astype(float) for s, g in bars.groupby("symbol")}
    marks = ",".join(f"'{s}'" for s in BROAD_ETFS)
    shares = con.execute(f"SELECT symbol, trade_date, shares FROM etf_shares WHERE symbol IN ({marks})").fetchdf()
    prices = con.execute(f"SELECT symbol, trade_date, close FROM etf_bars WHERE symbol IN ({marks})").fetchdf()
    for f in (shares, prices):
        f["trade_date"] = pd.to_datetime(f["trade_date"])
    sh = shares.pivot_table(index="trade_date", columns="symbol", values="shares")
    px = prices.pivot_table(index="trade_date", columns="symbol", values="close")
    sessions = idx["sh000300"].index
    sh = sh.reindex(sessions).ffill(limit=3)
    px = px.reindex(sessions).ffill(limit=3)
    flow = (sh.diff() * px).sum(axis=1, min_count=1)  # yuan created (+) or redeemed (-) per session
    flow = flow.loc["2016-10-10":]
    pe = cached(data / "csi300_pe_lg.parquet", lambda: ak.stock_index_pe_lg(symbol="沪深300"))
    pe = pd.Series(pe["滚动市盈率"].astype(float).to_numpy(), index=pd.to_datetime(pe["日期"]))
    congestion = cached(data / "congestion_lg.parquet", ak.stock_a_congestion_lg)
    congestion = pd.Series(congestion["congestion"].astype(float).to_numpy(), index=pd.to_datetime(congestion["date"])).dropna()
    margin = cached(data / "margin_sse.parquet", lambda: ak.stock_margin_sse(start_date="20100101", end_date="20261024"))
    margin = pd.Series(margin["融资余额"].astype(float).to_numpy(),
                       index=pd.to_datetime(margin["信用交易日期"].astype(str))).sort_index()
    rates = pd.read_parquet(rates_path)
    y10 = pd.Series(rates["中国国债收益率10年"].astype(float).to_numpy(), index=pd.to_datetime(rates["日期"])).dropna()
    return idx, flow, pe, congestion, margin, y10


def forward(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({f"f{n}": close.shift(-n) / close - 1 for n in (5, 20, 60)})


def events(flags: pd.Series, cooldown: int = 20) -> list[pd.Timestamp]:
    out, last = [], -10**9
    values = flags.fillna(False).to_numpy()
    for i, flag in enumerate(values):
        if flag and i - last > cooldown:
            out.append(flags.index[i])
            last = i
    return out


def report_events(name: str, days: list, fwd: pd.DataFrame, start=None) -> None:
    base = fwd.loc[start:] if start is not None else fwd
    e = fwd.reindex(days)
    parts = []
    for col, n in (("f5", 5), ("f20", 20), ("f60", 60)):
        x = e[col].dropna()
        b = base[col].dropna()
        parts.append(f"{n}日 {x.mean():+.1%}/{(x > 0).mean():.0%}（全部 {b.mean():+.1%}/{(b > 0).mean():.0%}）")
    print(f"{name}：{len(days)} 次；" + "；".join(parts))
    if days:
        print("   " + "，".join(f"{d.date()} {fwd.loc[d, 'f20']:+.0%}" for d in days[-10:] if d in fwd.index) +
              ("（20 日涨跌，最近 10 次）" if len(days) > 0 else ""))


def quintiles(name: str, x: pd.Series, fwd: pd.Series) -> None:
    j = pd.concat([x, fwd], axis=1, keys=["x", "f"]).dropna()
    if len(j) < 30:
        print(f"{name}：样本不足"); return
    j["q"] = pd.qcut(j["x"].rank(method="first"), 5, labels=["最低", "2", "3", "4", "最高"])
    g = j.groupby("q", observed=True)["f"]
    print(f"{name}（{len(j)} 个月，{j.index.min():%Y-%m}起）：" + "，".join(f"{k} {v.mean():+.1%}" for k, v in g) +
          f"；相关 {j['x'].corr(j['f']):+.2f}")


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 50) -> np.ndarray:
    """L2-regularised logistic regression by Newton's method (no scikit-learn in the production venv)."""
    x = np.column_stack([np.ones(len(x)), x])
    w = np.zeros(x.shape[1])
    penalty = np.eye(x.shape[1]) * l2
    penalty[0, 0] = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-x @ w))
        grad = x.T @ (p - y) + penalty @ w
        hess = (x * (p * (1 - p))[:, None]).T @ x + penalty
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def logistic_oos(month: pd.DataFrame, features: list[str], start: str) -> None:
    data = month.dropna(subset=features + ["f20"]).copy()
    data["up"] = (data["f20"] > 0).astype(int)
    preds = []
    for t in data.loc[start:].index:
        train = data.loc[: t - pd.DateOffset(months=1)].iloc[:-1]  # the last training label must be known by t
        if len(train) < 60 or train["up"].nunique() < 2:
            continue
        mu, sd = train[features].mean(), train[features].std()
        w = fit_logistic(((train[features] - mu) / sd).to_numpy(), train["up"].to_numpy())
        z = ((data.loc[t, features] - mu) / sd).to_numpy()
        preds.append((t, float(1 / (1 + np.exp(-(w[0] + z @ w[1:]))))))
    p = pd.Series(dict(preds))
    test = data.loc[p.index]
    hit = ((p.to_numpy() > 0.5).astype(int) == test["up"].to_numpy()).mean()
    print(f"样本外 {len(p)} 个月（{p.index.min():%Y-%m}–{p.index.max():%Y-%m}）：方向命中率 {hit:.0%}；"
          f"“总是猜涨”命中率 {test['up'].mean():.0%}")
    hi, lo = test["f20"][p.to_numpy() > 0.6], test["f20"][p.to_numpy() < 0.4]
    print(f"   预测上涨概率 > 60% 的月份 {len(hi)} 个，之后 20 日平均 {hi.mean():+.1%}；< 40% 的 {len(lo)} 个，平均 {lo.mean():+.1%}")
    strat = np.where(p.to_numpy() > 0.5, test["f20"].to_numpy(), 0.0)
    for label, r in (("按预测进出", strat), ("一直持有", test["f20"].to_numpy())):
        curve = np.cumprod(1 + r)
        print(f"   {label}：累计 {curve[-1] - 1:+.0%}，最大回撤 {(curve / np.maximum.accumulate(curve) - 1).min():.0%}，"
              f"持仓月份 {np.mean(r != 0):.0%}（未计成本，月度不重叠）")


def main() -> None:
    database, data, rates_path = sys.argv[1], Path(sys.argv[2]).expanduser(), sys.argv[3]
    idx, flow, pe, congestion, margin, y10 = load(database, data, rates_path)
    for sym, name, drop5 in (("sh000300", "沪深300", -0.07), ("sz399006", "创业板指", -0.09)):
        d = idx[sym]
        close, turn = d["close"], d["turnover_cny"]
        fwd = forward(close)
        dd = close / close.rolling(250, min_periods=60).max() - 1
        r60 = close / close.shift(60) - 1
        spike = turn >= 2 * turn.rolling(60, min_periods=60).mean().shift(1)
        print(f"\n################ {name}（{close.index[0].date()}–{close.index[-1].date()}）")
        print("== 事件（之后平均涨跌 / 上涨比例；括号里是所有交易日）")
        report_events("E1 跌久放巨量", events((dd <= -0.20) & (r60 <= -0.10) & spike), fwd)
        # a looser second try (added after seeing E1 fire only in 2008 on CSI 300): drawdown 15%, turnover 1.5x
        loose = turn >= 1.5 * turn.rolling(60, min_periods=60).mean().shift(1)
        report_events("E1b 放宽：回撤 15%、成交 1.5 倍（看过 E1 后加的）", events((dd <= -0.15) & (r60 <= -0.08) & loose), fwd)
        f = flow.shift(1).reindex(close.index)  # shares are published after the close (SSE ~23:00, SZSE next morning)
        f5 = f.rolling(5, min_periods=5).sum()
        thr = f5.expanding(min_periods=250).quantile(0.98).shift(1)
        big = f5 > thr
        report_events("E2 宽基 ETF 大额申购（5 日净申购创新高档）", events(big), fwd, start="2017-10-01")
        report_events("E3 E2 且在回撤 15% 以上时", events(big & (dd <= -0.15)), fwd, start="2017-10-01")
        report_events(f"E4 5 日跌超 {abs(drop5):.0%}", events(close / close.shift(5) - 1 <= drop5), fwd)

        print("== 连续指标（月末，按五档分组，之后平均涨跌）")
        me = close.resample("ME").last().index
        at = close.index[close.index.get_indexer(me, method="pad")]
        m = pd.DataFrame(index=at)
        m["f20"], m["f60"] = fwd["f20"].reindex(at), fwd["f60"].reindex(at)
        m["f250"] = (close.shift(-250) / close - 1).reindex(at)
        m["mom120"] = (close / close.shift(120) - 1).reindex(at)
        m["heat"] = (turn.rolling(20).mean() / turn.rolling(250).mean()).reindex(at)
        m["ret5"] = (close / close.shift(5) - 1).reindex(at)
        m["ret20"] = (close / close.shift(20) - 1).reindex(at)
        pe_d = pe.reindex(close.index, method="ffill")
        y_d = y10.reindex(close.index, method="ffill")
        m["erp"] = (1 / pe_d - y_d / 100).reindex(at)
        m["cong"] = congestion.reindex(close.index, method="ffill").reindex(at)
        mg = margin.reindex(close.index, method="ffill")
        m["margin20"] = (mg / mg.shift(20) - 1).reindex(at)
        m["flow20"] = f.rolling(20, min_periods=15).sum().reindex(at)
        quintiles("P1 过去 120 日涨跌 → 之后 20 日", m["mom120"], m["f20"])
        quintiles("P2 成交热度（20 日 / 250 日成交额）→ 之后 60 日", m["heat"], m["f60"])
        quintiles("P3 拥挤度 → 之后 60 日", m["cong"], m["f60"])
        quintiles("P4 股债性价比（沪深300 盈利收益率 − 10 年国债）→ 之后 60 日", m["erp"], m["f60"])
        quintiles("P4 股债性价比 → 之后 250 日", m["erp"], m["f250"])
        quintiles("P5 融资余额 20 日变化 → 之后 20 日", m["margin20"], m["f20"])
        quintiles("P6 宽基 ETF 20 日净申购额 → 之后 20 日", m["flow20"], m["f20"])
        print("== 合起来：逻辑回归，每月用之前的数据重新拟合，预测之后 20 日涨跌")
        logistic_oos(m, ["mom120", "heat", "erp", "ret5", "ret20"], start="2012-01-01")
        if sym == "sh000300":
            print(f"   现在：股债性价比 {m['erp'].iloc[-1]:.2%}（2005 年以来分位 {(m['erp'] < m['erp'].iloc[-1]).mean():.0%}），"
                  f"成交热度 {m['heat'].iloc[-1]:.2f}，过去 120 日 {m['mom120'].iloc[-1]:+.0%}")


if __name__ == "__main__":
    main()
