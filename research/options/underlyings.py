"""The indices behind the listed ETF options: constituents, industries, size, valuation, risk, and option activity.
Question of 2026-10-18.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/options/underlyings.py \
        data/market.duckdb ~/L1/research/options/data

Sources: constituent weights from CSI (上证50, 科创50, 沪深300, 中证500) and CNI (创业板指, 深证100), as of their
latest month end; industries from industry_sw (SW 2021 L1); market caps from market_snapshot; index closes from
index_bars (深证100 from Sina); CSI's published PE and dividend yield, and for the CNI indices a TTM PE computed
here from fin_income (parent net profit, latest report published by the snapshot day); option activity from the
exchanges' daily statistics (research download, to 2026-10-09) and SSE implied volatility from option_risk.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import akshare as ak
import duckdb
import numpy as np
import pandas as pd

INDICES = {  # code: (name, index_bars symbol, weight source, option underlyings)
    "000016": ("上证50", "sh000016", "csi", "510050 华夏上证50ETF（上交所）、HO 股指期权（中金所）"),
    "000300": ("沪深300", "sh000300", "db", "510300 华泰柏瑞（上交所）、159919 嘉实（深交所）、IO 股指期权（中金所）"),
    "000905": ("中证500", "sh000905", "db", "510500 南方（上交所）、159922（深交所）"),
    "000688": ("科创50", "sh000688", "csi", "588000 华夏、588080 易方达（上交所）"),
    "399006": ("创业板指", "sz399006", "cni", "159915 易方达创业板ETF（深交所）"),
    "399330": ("深证100", "sz399330", "cni", "159901（深交所）"),
    "000852": ("中证1000", "sh000852", "db", "MO 股指期权（中金所）；没有 ETF 期权"),
}
SSE_ETF = {"000016": "510050", "000300": "510300", "000905": "510500", "000688": "588000"}


def weights(con, code: str, source: str) -> pd.DataFrame:
    if source == "db":
        w = con.execute("SELECT symbol, name, weight FROM index_weights WHERE index_code = ? AND as_of_date = "
                        "(SELECT max(as_of_date) FROM index_weights WHERE index_code = ?)", [code, code]).fetchdf()
        w["weight"] = w["weight"].astype(float) * 100
    elif source == "csi":
        raw = ak.index_stock_cons_weight_csindex(symbol=code)
        w = pd.DataFrame({"symbol": raw["成分券代码"].astype(str), "name": raw["成分券名称"], "weight": raw["权重"].astype(float)})
    else:
        raw = ak.index_detail_cni(symbol=code)
        w = pd.DataFrame({"symbol": raw["样本代码"].astype(str).str.zfill(6), "name": raw["样本简称"],
                          "weight": raw["权重"].astype(float)})
    w["weight"] = w["weight"] / w["weight"].sum() * 100
    return w.sort_values("weight", ascending=False, ignore_index=True)


def ttm_profit(con, asof: date) -> pd.Series:
    inc = con.execute("SELECT symbol, report_date, parent_net_profit, version FROM fin_income WHERE notice_date <= ?",
                      [asof]).fetchdf()
    inc = inc.sort_values("version").drop_duplicates(["symbol", "report_date"], keep="last")
    inc["report_date"] = pd.to_datetime(inc["report_date"])
    value = inc.set_index(["symbol", "report_date"])["parent_net_profit"]
    out = {}
    for symbol, g in inc.groupby("symbol"):
        latest = g["report_date"].max()
        if latest.month == 12:
            out[symbol] = value.get((symbol, latest), np.nan)
            continue
        annual = value.get((symbol, pd.Timestamp(latest.year - 1, 12, 31)), np.nan)
        before = value.get((symbol, latest - pd.DateOffset(years=1)), np.nan)
        out[symbol] = value.get((symbol, latest), np.nan) + annual - before
    return pd.Series(out)


def stats(close: pd.Series) -> dict:
    close = close.dropna()
    r = np.log(close).diff().dropna()
    end = close.index[-1]

    def back(years):
        start = close[close.index <= end - pd.DateOffset(years=years)]
        return close.iloc[-1] / start.iloc[-1] - 1 if len(start) else np.nan

    ten = close[close.index >= end - pd.DateOffset(years=10)]
    dd = (ten / ten.cummax() - 1).min()
    yearly = close.groupby(close.index.year).last().pct_change()
    ytd = close.iloc[-1] / close[close.index < pd.Timestamp(end.year, 1, 1)].iloc[-1] - 1
    return {"今年以来": ytd, "近1年": back(1), "近3年": back(3), "近5年": back(5),
            "近1年波动率": r.iloc[-250:].std() * np.sqrt(250), "近10年波动率": r[r.index >= ten.index[0]].std() * np.sqrt(250),
            "近10年最大回撤": dd, "近10年最差年份": f"{yearly.loc[yearly.index >= end.year - 10].idxmin()} "
                                                 f"{yearly.loc[yearly.index >= end.year - 10].min():+.0%}"}


def main() -> None:
    database, data = Path(sys.argv[1]), Path(sys.argv[2]).expanduser()
    con = duckdb.connect(str(database), read_only=True)
    asof = con.execute("SELECT max(snapshot_date) FROM market_snapshot").fetchone()[0]
    caps = con.execute("SELECT symbol, market_cap_cny FROM market_snapshot WHERE snapshot_date = ?", [asof]).fetchdf() \
        .set_index("symbol")["market_cap_cny"]
    industry = con.execute("SELECT symbol, arg_max(l1_name, start_date) AS l1, arg_max(sw_code, start_date) AS code "
                           "FROM industry_sw GROUP BY 1").fetchdf()
    # configs/industry/sw2014_to_sw2021_l1.json maps SW2014 330102 (空调: 美的, 格力) to 房地产, decided by a single
    # stock; it is 家用电器 (ledger, 2026-10-18).
    industry.loc[industry["code"] == "330102", "l1"] = "家用电器"
    industry = industry.set_index("symbol")["l1"]
    profit = ttm_profit(con, asof)
    closes = {}
    for code, (name, symbol, source, _) in INDICES.items():
        bars = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY 1", [symbol]).fetchdf()
        if bars.empty:
            raw = ak.stock_zh_index_daily(symbol=symbol)
            bars = pd.DataFrame({"trade_date": raw["date"], "close": raw["close"]})
        closes[code] = pd.Series(bars["close"].astype(float).to_numpy(), index=pd.to_datetime(bars["trade_date"]))
    print(f"as of {asof}")
    summary = {}
    for code, (name, symbol, source, options) in INDICES.items():
        w = weights(con, code, source)
        time.sleep(1)
        w["cap"] = w["symbol"].map(caps) / 1e8
        w["industry"] = w["symbol"].map(industry).fillna("未分类")
        ep = (w["symbol"].map(profit) / (w["cap"] * 1e8)).astype(float)
        covered = ep.notna()
        pe = 1 / np.nansum(w.loc[covered, "weight"] * ep[covered]) * w.loc[covered, "weight"].sum()
        inds = w.groupby("industry")["weight"].sum().sort_values(ascending=False)
        s = stats(closes[code])
        summary[name] = {"成分股": len(w), "前10大权重": w["weight"].head(10).sum(),
                         "加权平均市值（亿）": np.nansum(w["weight"] * w["cap"]) / 100, "市值中位数（亿）": w["cap"].median(),
                         "PE（本表算，TTM）": pe, **s}
        print(f"\n=== {code} {name}：{len(w)} 只；期权：{options}")
        print("前十大：" + "、".join(f"{r.name}({r.industry}) {r.weight:.1f}%" for r in w.head(10).itertuples()))
        print("行业：" + "、".join(f"{k} {v:.1f}%" for k, v in inds.head(6).items()) + f"；共 {len(inds)} 个一级行业")
        print(f"PE 覆盖权重 {w.loc[covered, 'weight'].sum():.0f}%")
    table = pd.DataFrame(summary).T
    fmt = {c: "{:+.1%}".format for c in ("今年以来", "近1年", "近3年", "近5年", "近10年最大回撤")}
    fmt.update({c: "{:.1%}".format for c in ("近1年波动率", "近10年波动率", "前10大权重")})
    print("\n" + table.to_string(formatters={**fmt, "前10大权重": lambda v: f"{v:.0f}%",
                                              "加权平均市值（亿）": "{:,.0f}".format, "市值中位数（亿）": "{:,.0f}".format,
                                              "PE（本表算，TTM）": "{:.1f}".format}))
    for code in ("000016", "000300", "000905", "000688"):
        v = ak.stock_zh_index_value_csindex(symbol=code).iloc[0]
        print(f"CSI 公布 {INDICES[code][0]}：市盈率 {v['市盈率1']}，股息率 {v['股息率1']}%（{v['日期']}）")
        time.sleep(1)
    rets = pd.DataFrame({INDICES[c][0]: np.log(s).diff() for c, s in closes.items()}).dropna().iloc[-250:]
    print("\n近1年日收益相关系数：\n" + rets.corr().round(2).to_string())

    # option activity: exchanges' daily statistics (last 20 sessions to 2026-10-09)
    sse = pd.read_parquet(data / "stats_sse.parquet")
    sse["交易日"] = pd.to_datetime(sse["交易日"])
    last = sse[sse["交易日"] >= sse["交易日"].max() - pd.Timedelta(days=30)]
    print("\n上交所（近一个月日均）：")
    print(last.groupby(["合约标的代码", "合约标的名称"])[["总成交量", "总成交额", "未平仓合约总数"]].mean().round(0).to_string())
    szse = pd.read_parquet(data / "stats_szse.parquet")
    print("\n深交所统计列：", list(szse.columns))
    date_col = next(c for c in szse.columns if "日期" in c or "交易日" in c)
    szse[date_col] = pd.to_datetime(szse[date_col])
    recent = szse[szse[date_col] >= szse[date_col].max() - pd.Timedelta(days=30)]
    key = [c for c in szse.columns if "标的" in c and ("名称" in c or "代码" in c)] or [szse.columns[1]]
    num = [c for c in szse.columns if any(k in c for k in ("成交量", "持仓", "未平仓")) and szse[c].dtype != object]
    print(recent.groupby(key)[num].mean().round(0).to_string())

    # SSE at-the-money implied volatility now and its 3-year range
    risk = con.execute("SELECT r.underlying, r.trade_date, r.iv, r.delta, c.expiry_date FROM option_risk r JOIN "
                       "option_contracts c USING (contract) WHERE r.trade_date >= DATE '2023-06-01' AND c.kind = 'call' "
                       "AND NOT c.adjusted AND r.iv > 0").fetchdf()
    risk["left"] = (pd.to_datetime(risk["expiry_date"]) - pd.to_datetime(risk["trade_date"])).dt.days
    risk = risk[(risk["left"] >= 10) & risk["delta"].between(0.4, 0.6)]
    risk = risk[risk["expiry_date"] == risk.groupby(["underlying", "trade_date"])["expiry_date"].transform("min")]
    iv = risk.groupby(["underlying", "trade_date"])["iv"].mean().unstack(0)
    print("\n上交所平值认购隐含波动率（近月、剩余至少 10 天）：")
    for u in iv.columns:
        s = iv[u].dropna()
        print(f"{u}: 最新 {s.iloc[-1]:.1%}（{s.index[-1]}），2023-06 以来中位数 {s.median():.1%}，"
              f"10%–90% 区间 {s.quantile(.1):.1%}–{s.quantile(.9):.1%}，最新所处分位 {(s < s.iloc[-1]).mean():.0%}")


if __name__ == "__main__":
    main()
