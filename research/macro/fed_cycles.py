"""Fed rate cycles and A-share bull and bear markets (question of 2026-10-24): does a Fed hiking cycle (or strong
hike expectations) bring an A-share bear market, and a cutting cycle a bull market?  Can bull and bear markets be
measured and predicted?

    cd ~/L1/MinervaQuantSystem && PYTHONPATH=src .venv/bin/python ~/L1/research/macro/fed_cycles.py \
        data/market.duckdb ~/L1/research/macro/data

Data: data/fed_target.csv (parse_fed.py, federalreserve.gov, effective dates); US and Chinese government bond
yields (AKShare bond_zh_us_rate, Eastmoney, cached as data/us_cn_rates.parquet); USD/CNY central parity (Bank of
China via Sina, cached as data/usdcny.parquet); CSI 300 (sh000300, from 2005-04) and the Shanghai composite
(sh000001) from index_bars.

Definitions, fixed before looking at the results:
  bull / bear   after the fact: alternating peaks and troughs at least 20% apart (research/regime, bull_bear);
                a bear market runs from a peak to the next trough
  real-time     close vs its 200-session average; 250-session return; the 20% rule as it confirms (the day the
                move from the last extreme reaches 20%)
  Fed phases    hiking: first to last hike of a run of hikes; high hold: last hike to first cut; cutting: first
                to last cut; low hold: last cut to first hike (the end of a run is known only afterwards)
  Fed at t      (known then) the direction of the last change and whether it was within 12 months
  expectations  6-month change of the US 2-year yield at a month end: > +0.5 point "hikes priced in", < -0.5
                "cuts priced in", else neutral
  forward       CSI 300 price return from that month end over the next 6 and 12 months (overlapping windows:
                independent observations are about months / 6 or / 12)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak
import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "regime"))
from regime_factor_study import bull_bear  # noqa: E402


def load(database: str, data: Path):
    con = duckdb.connect(database, read_only=True)
    idx = con.execute("SELECT symbol, trade_date, close FROM index_bars WHERE symbol IN ('sh000300', 'sh000001') "
                      "ORDER BY 2").fetchdf()
    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    closes = {s: g.set_index("trade_date")["close"].astype(float) for s, g in idx.groupby("symbol")}
    fed = pd.read_csv(data / "fed_target.csv", parse_dates=["date"])
    rates_path = data / "us_cn_rates.parquet"
    if not rates_path.exists():
        raw = ak.bond_zh_us_rate(start_date="20020101")
        raw.to_parquet(rates_path, index=False)
        time.sleep(1)
    rates = pd.read_parquet(rates_path)
    rates["日期"] = pd.to_datetime(rates["日期"])
    rates = rates.set_index("日期")
    fx_path = data / "usdcny.parquet"
    if not fx_path.exists():
        ak.currency_boc_sina(symbol="美元", start_date="20050101", end_date=pd.Timestamp.today().strftime("%Y%m%d")) \
            .to_parquet(fx_path, index=False)
    fx = pd.read_parquet(fx_path)
    fx = pd.Series(pd.to_numeric(fx["央行中间价"], errors="coerce").to_numpy() / 100,
                   index=pd.to_datetime(fx["日期"])).dropna()
    return closes, fed, rates, fx


def turns(close: pd.Series) -> pd.DataFrame:
    """Peaks and troughs of the 20% rule, with the move to the next turn."""
    label = bull_bear(close)
    starts = label.ne(label.shift()) & label.notna()
    rows = []
    points = list(label.index[starts]) + [close.index[-1]]
    for a, b in zip(points[:-1], points[1:]):
        kind = label.loc[a]
        rows.append({"phase": kind, "start": a.date(), "end": b.date(), "level_start": close.loc[a], "level_end": close.loc[b],
                     "change": close.loc[b] / close.loc[a] - 1, "months": (b - a).days / 30.4})
    return pd.DataFrame(rows)


def realtime_lags(close: pd.Series, table: pd.DataFrame) -> pd.DataFrame:
    """For each turn: how long after the extreme each real-time indicator flipped, and how far the index had moved."""
    ma200 = close.rolling(200, min_periods=200).mean()
    mom = close / close.shift(250) - 1
    rows = []
    for r in table.itertuples(index=False):
        t = pd.Timestamp(r.start)
        after = close.loc[t:]
        up = r.phase == "牛市"  # a trough
        out = {"turn": ("底 " if up else "顶 ") + str(r.start), "level": round(r.level_start, 0)}
        conds = {
            "20% 规则确认": (after >= after.iloc[0] * 1.2) if up else (after <= after.iloc[0] * 0.8),
            "上穿/跌破 200 日线": (after > ma200.loc[t:]) if up else (after < ma200.loc[t:]),
            "一年涨跌翻正/翻负": (mom.loc[t:] > 0) if up else (mom.loc[t:] < 0),
        }
        for name, cond in conds.items():
            hit = cond[cond].index
            if len(hit) == 0:
                out[name] = "未发生"
                continue
            d = hit[0]
            if d == t:  # a short swing inside a longer trend: the indicator never flipped the other way
                out[name] = "转折时已是这个状态（没有翻转）"
                continue
            moved = close.loc[d] / close.loc[t] - 1
            out[name] = f"{round((d - t).days / 30.4)} 个月后，已{'涨' if moved > 0 else '跌'} {abs(moved):.0%}"
        rows.append(out)
    return pd.DataFrame(rows)


def fed_phases(fed: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Runs of same-signed changes; between runs the hold phases."""
    f = fed[fed["date"] >= "2003-01-01"].reset_index(drop=True)
    f["sign"] = np.sign(f["change_bp"])
    f["run"] = (f["sign"] != f["sign"].shift()).cumsum()
    runs = f.groupby("run").agg(sign=("sign", "first"), first=("date", "min"), last=("date", "max"),
                                start_level=("level", lambda x: x.iloc[0]), end_level=("level", "last"), n=("date", "size"))
    rows = []
    for i, r in enumerate(runs.itertuples(index=False)):
        rows.append({"phase": "加息" if r.sign > 0 else "降息", "start": r.first, "end": r.last,
                     "detail": f"{r.n} 次，到 {r.end_level:.2f}%"})
        nxt = runs.iloc[i + 1]["first"] if i + 1 < len(runs) else today
        rows.append({"phase": "高位按兵不动" if r.sign > 0 else "低位按兵不动", "start": r.last, "end": nxt,
                     "detail": f"维持 {r.end_level:.2f}%"})
    return pd.DataFrame(rows)


def period_return(close: pd.Series, a, b) -> float:
    s = close.loc[pd.Timestamp(a):pd.Timestamp(b)]
    return s.iloc[-1] / s.iloc[0] - 1 if len(s) > 1 else np.nan


def main() -> None:
    database, data = sys.argv[1], Path(sys.argv[2]).expanduser()
    closes, fed, rates, fx = load(database, data)
    hs300, sh = closes["sh000300"], closes["sh000001"]
    today = hs300.index[-1]
    print(f"数据：沪深300 {hs300.index[0].date()}–{today.date()}；美联储利率变动 {len(fed)} 次，最近 {fed['date'].max().date()}；"
          f"美债收益率到 {rates.index.max().date()}")

    print("\n== 1. 事后划分的牛熊（沪深300，涨跌 20% 为界）")
    table = turns(hs300)
    print(table.to_string(index=False, formatters={"change": "{:+.0%}".format, "months": "{:.0f}".format, "level_start": "{:.0f}".format,
                                                   "level_end": "{:.0f}".format}))
    days = bull_bear(hs300).value_counts()
    print(f"牛市 {days.get('牛市', 0)} 个交易日，熊市 {days.get('熊市', 0)} 个交易日；最后一段是到今天为止、尚未被下一次 20% 反向确认的走势")

    print("\n== 2. 实时指标滞后多少（每个顶和底之后，指标第一次翻转的时间和当时已走的幅度）")
    print(realtime_lags(hs300, table).to_string(index=False))

    print("\n== 3. 美联储各阶段里 A 股的表现")
    phases = fed_phases(fed, today)
    label = bull_bear(sh)
    rows = []
    for r in phases.itertuples(index=False):
        a, b = max(r.start, pd.Timestamp("2004-01-02")), r.end
        if b <= pd.Timestamp("2004-01-02"):
            continue
        span = label.loc[a:b].dropna()
        years = (b - a).days / 365.25
        hs = period_return(hs300, a, b) if a >= hs300.index[0] else np.nan
        rows.append({"阶段": r.phase, "起": a.date(), "止": b.date(), "说明": r.detail, "年数": years,
                     "上证": period_return(sh, a, b), "沪深300": hs,
                     "上证年化": (1 + period_return(sh, a, b)) ** (1 / years) - 1 if years > 0.25 else np.nan,
                     "牛市天数占比": (span == "牛市").mean() if len(span) else np.nan,
                     "美债2年变化": rates["美国国债收益率2年"].loc[a:b].dropna().iloc[[0, -1]].diff().iloc[-1]})
    t = pd.DataFrame(rows)
    print(t.to_string(index=False, formatters={"年数": "{:.1f}".format, "上证": "{:+.0%}".format,
                                               "沪深300": lambda v: "" if v != v else f"{v:+.0%}",
                                               "上证年化": lambda v: "" if v != v else f"{v:+.0%}",
                                               "牛市天数占比": "{:.0%}".format, "美债2年变化": "{:+.2f}".format}))
    for name in ("加息", "高位按兵不动", "降息", "低位按兵不动"):
        g = t[t["阶段"] == name]
        print(f"{name}：{len(g)} 段，上证年化收益的平均 {g['上证年化'].mean():+.0%}，各段 "
              + "、".join(f"{v:+.0%}" for v in g["上证年化"].dropna()))

    print("\n== 4. 用月底已知的信息，看沪深300 之后 6 个月、12 个月的涨跌")
    month_ends = hs300.resample("ME").last().dropna()
    me = pd.DataFrame(index=month_ends.index)
    me["fwd6"] = month_ends.shift(-6) / month_ends - 1
    me["fwd12"] = month_ends.shift(-12) / month_ends - 1
    fed_d = fed.set_index("date")["change_bp"]
    last_dir, recent = [], []
    for d in me.index:
        past = fed_d.loc[:d]
        last_dir.append("最近一次是加息" if past.iloc[-1] > 0 else "最近一次是降息")
        yr = past.loc[d - pd.DateOffset(months=12):]
        recent.append("一年内加过息" if (yr > 0).any() else ("一年内降过息" if (yr < 0).any() else "一年内没动"))
    me["fed_last"], me["fed_12m"] = last_dir, recent
    us2 = rates["美国国债收益率2年"].dropna()
    us2m = us2.resample("ME").last()
    me["us2_6m"] = (us2m - us2m.shift(6)).reindex(me.index)
    me["预期"] = np.select([me["us2_6m"] > 0.5, me["us2_6m"] < -0.5], ["加息预期升温（2年美债半年 +0.5 以上）",
                                                                     "降息预期升温（-0.5 以下）"], "中性")
    me.loc[me["us2_6m"].isna(), "预期"] = None
    ma200 = hs300.rolling(200, min_periods=200).mean()
    ma200m = ma200.resample("ME").last().reindex(me.index)  # month ends are calendar days: take the last session
    me["ma200"] = np.where(month_ends > ma200m, "200 日线上方", "200 日线下方")
    me.loc[ma200m.isna(), "ma200"] = None
    mom_daily = hs300 / hs300.shift(250) - 1
    mom = mom_daily.resample("ME").last().reindex(me.index)
    me["mom12"] = np.where(mom > 0, "过去一年上涨", "过去一年下跌")
    me.loc[mom.isna(), "mom12"] = None
    spread = (rates["中国国债收益率10年"] - rates["美国国债收益率10年"]).resample("ME").last().reindex(me.index)
    me["spread_chg"] = spread - spread.shift(6)
    fxm = fx.resample("ME").last().reindex(me.index)
    me["cny_6m"] = fxm / fxm.shift(6) - 1  # + : the yuan weakened
    base = me.dropna(subset=["fwd6"])
    print(f"全部月份：之后 6 个月平均 {base['fwd6'].mean():+.1%}（上涨 {(base['fwd6'] > 0).mean():.0%}，{len(base)} 个月），"
          f"之后 12 个月平均 {base['fwd12'].mean():+.1%}（上涨 {(base['fwd12'].dropna() > 0).mean():.0%}）")

    def show(key: str, frame: pd.DataFrame) -> None:
        g = frame.dropna(subset=[key, "fwd6"]).groupby(key)
        out = pd.DataFrame({"月数": g.size(), "6个月平均": g["fwd6"].mean(), "6个月上涨比例": g["fwd6"].apply(lambda x: (x > 0).mean()),
                            "12个月平均": g["fwd12"].mean(), "12个月上涨比例": g["fwd12"].apply(lambda x: (x.dropna() > 0).mean())})
        print(out.to_string(formatters={"6个月平均": "{:+.1%}".format, "6个月上涨比例": "{:.0%}".format,
                                        "12个月平均": "{:+.1%}".format, "12个月上涨比例": "{:.0%}".format}))
        # non-overlapping: only June and December month ends, so the 6-month windows do not overlap
        half = frame[frame.index.month.isin([6, 12])].dropna(subset=[key, "fwd6"]).groupby(key)["fwd6"]
        print("   不重叠的半年样本（只用 6 月底、12 月底）：" + "；".join(
            f"{k} {g.mean():+.1%} ± {g.std(ddof=1) / np.sqrt(len(g)):.1%}（{len(g)} 个）" for k, g in half))

    for key, title in (("fed_last", "美联储最近一次动作"), ("fed_12m", "美联储一年内的动作"), ("预期", "市场的加息预期"),
                       ("ma200", "A 股自己：200 日线"), ("mom12", "A 股自己：过去一年涨跌")):
        print(f"\n-- {title}（2005-05 至今）")
        show(key, me)
        for lo, hi in (("2005", "2014"), ("2015", "2026")):
            part = me.loc[lo:hi]
            g = part.dropna(subset=[key, "fwd6"]).groupby(key)["fwd6"].agg(["mean", "size"])
            print(f"   {lo}–{hi}：" + "；".join(f"{k} {v['mean']:+.1%}（{int(v['size'])}）" for k, v in g.iterrows()))

    print("\n-- 每次“开启”加息或降息之后（第一次加息、第一次降息的生效日；2004 年用上证，之后用沪深300）")
    f = fed[fed["date"] >= "2004-01-01"].reset_index(drop=True)
    starts = f[np.sign(f["change_bp"]) != np.sign(f["change_bp"].shift())]
    for r in starts.itertuples(index=False):
        s = hs300 if r.date >= hs300.index[0] else sh
        name = "沪深300" if s is hs300 else "上证"
        at = s.index[s.index.get_indexer([r.date], method="bfill")[0]]
        parts = []
        for months in (-6, 6, 12, 24):
            other = at + pd.DateOffset(months=months)
            if other > s.index[-1] or other < s.index[0]:
                parts.append(f"{months:+d} 个月 —")
                continue
            v = s.asof(other)
            ret = v / s.loc[at] - 1 if months > 0 else s.loc[at] / v - 1
            parts.append(f"{'前' if months < 0 else '后'} {abs(months)} 个月 {ret:+.0%}")
        print(f"{r.date.date()} 开始{'加息' if r.change_bp > 0 else '降息'}（{name}）：" + "，".join(parts))

    print("\n-- 相关系数：月底的指标 vs 沪深300 之后 6 个月涨跌（同期：过去 6 个月）")
    past6 = month_ends / month_ends.shift(6) - 1
    for col, name in (("us2_6m", "2年美债过去半年变化"), ("spread_chg", "中美10年利差过去半年变化"), ("cny_6m", "人民币过去半年贬值幅度")):
        x = me[col]
        j = pd.concat([x, me["fwd6"], past6], axis=1, keys=["x", "fwd", "past"]).dropna()
        print(f"{name}：与之后 6 个月 {j['x'].corr(j['fwd']):+.2f}；与同期（过去 6 个月）{j['x'].corr(j['past']):+.2f}（{len(j)} 个月）")

    print("\n== 5. 现在")
    last = me.index[-1]
    print(f"截至 {today.date()}：美联储最近一次动作 {fed['date'].max().date()} {fed['change_bp'].iloc[-1]:+.0f} 基点到 "
          f"{fed['level_text'].iloc[-1]}%；2年美债 {us2.iloc[-1]:.2f}%，半年变化 {me.loc[last, 'us2_6m']:+.2f}；"
          f"10年美债 {rates['美国国债收益率10年'].dropna().iloc[-1]:.2f}%，中国10年 {rates['中国国债收益率10年'].dropna().iloc[-1]:.2f}%")
    print(f"沪深300 {hs300.iloc[-1]:.0f}，200 日线 {ma200.iloc[-1]:.0f}（{'上方' if hs300.iloc[-1] > ma200.iloc[-1] else '下方'}），"
          f"过去一年 {mom_daily.iloc[-1]:+.1%}，离一年内高点 {hs300.iloc[-1] / hs300.iloc[-250:].max() - 1:+.1%}")
    print(f"美元兑人民币中间价 {fx.iloc[-1]:.4f}，半年变化 {me.loc[last, 'cny_6m']:+.1%}")


if __name__ == "__main__":
    main()
