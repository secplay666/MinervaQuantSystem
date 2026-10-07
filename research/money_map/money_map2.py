"""Money map research, round 2 (2026-10-06): SW official L1 indices (their close and turnover), as the streamer uses.

1. Fetch the 31 SW L1 index histories (cached to sw_l1.parquet).
2. Candidate crowding measures C from each index's share of the 31 indices' turnover, calibrated on values from his screen.
3. Test his claims: C > threshold entries, C buckets, the zone grid, how long a crowded spell lasts.
   Excess returns vs the equal-weight average of the 31 industries and vs the CSI 300.

usage: python money_map2.py [REFETCH]
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
L1 = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁", "801050": "有色金属", "801080": "电子",
    "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服饰", "801140": "轻工制造", "801150": "医药生物",
    "801160": "公用事业", "801170": "交通运输", "801180": "房地产", "801200": "商贸零售", "801210": "社会服务",
    "801230": "综合", "801710": "建筑材料", "801720": "建筑装饰", "801730": "电力设备", "801740": "国防军工",
    "801750": "计算机", "801760": "传媒", "801770": "通信", "801780": "银行", "801790": "非银金融",
    "801880": "汽车", "801890": "机械设备", "801950": "煤炭", "801960": "石油石化", "801970": "环保",
    "801980": "美容护理",
}
# (as-of date, industry, C on screen, its 60-day change, 3-month return, 12-month return); None = not shown.
SCREEN = [
    ("2026-09-30", "建筑材料", 1.86, None, None, 0.10),
    ("2026-09-30", "通信", 1.47, 0.00, -0.17, 0.41),
    ("2025-02-07", "计算机", 1.21, -0.30, 0.05, 0.33),
    ("2025-02-07", "公用事业", 0.63, -0.02, -0.05, 0.06),
    ("2025-02-07", "煤炭", 0.52, 0.06, -0.09, -0.06),
    ("2025-01-10", "房地产", 0.58, -0.48, -0.08, -0.10),
    ("2024-09-13", "银行", 1.75, None, None, None),
    ("2024-09-13", "电子", 1.53, None, None, None),
]


def fetch(refetch: bool) -> pd.DataFrame:
    cache = HERE / "sw_l1.parquet"
    if cache.exists() and not refetch:
        return pd.read_parquet(cache)
    import akshare as ak

    frames = []
    for code, name in L1.items():
        frame = ak.index_hist_sw(symbol=code, period="day")
        frame["industry"] = name
        frames.append(frame)
        time.sleep(0.5)
    data = pd.concat(frames, ignore_index=True)
    data["日期"] = pd.to_datetime(data["日期"])
    data.to_parquet(cache)
    return data


def csi300() -> pd.Series:
    import duckdb

    root = "/home/van/L1/minerva-dev/data/canonical/index_bars"
    frame = duckdb.connect().execute(
        f"select trade_date, close from read_parquet('{root}/**/*.parquet', hive_partitioning=true) "
        "where symbol in ('sh000300', '000300') order by trade_date").fetchdf()
    return frame.set_index(pd.to_datetime(frame["trade_date"]))["close"]


def candidates(share: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for short in (5, 10, 20, 60):
        for long in (120, 250, 500, 750):
            out[f"share_{short}_{long}"] = share.rolling(short).mean() / share.rolling(long, min_periods=int(long * 0.9)).mean()
    out["share_20_vs_median250"] = share.rolling(20).mean() / share.rolling(250).median()
    out["share_20_vs_ewm250"] = share.rolling(20).mean() / share.ewm(span=250).mean()
    return out


def calibrate(close: pd.DataFrame, cands: dict[str, pd.DataFrame], report) -> str:
    rows = []
    for day, industry, c, dc, r3, r12 in SCREEN:
        at = close.index[close.index <= pd.Timestamp(day)][-1]
        i = close.index.get_loc(at)
        row = {"date": day, "industry": industry, "C_screen": c, "C60_screen": None if dc is None else c - dc,
               "r3_screen": r3, "r3_sw": close[industry].iloc[i] / close[industry].iloc[i - 60] - 1,
               "r12_screen": r12, "r12_sw": close[industry].iloc[i] / close[industry].iloc[i - 250] - 1}
        for name, frame in cands.items():
            row[name] = frame[industry].iloc[i]
            row[name + "@60"] = frame[industry].iloc[i - 60]
        rows.append(row)
    table = pd.DataFrame(rows)
    errors = {}
    for name in cands:
        now = (table[name] - table["C_screen"]).abs()
        before = (table[name + "@60"] - table["C60_screen"].astype(float)).abs().dropna()
        errors[name] = float(pd.concat([now, before]).mean())
    ranked = sorted(errors.items(), key=lambda kv: kv[1])
    report("calibration, mean abs error over screen values (C now and 60 sessions earlier):")
    for name, error in ranked[:8]:
        report(f"  {name:24s} {error:.3f}")
    best = ranked[0][0]
    shown = ["date", "industry", "r3_screen", "r3_sw", "r12_screen", "r12_sw", "C_screen", best, "C60_screen", best + "@60"]
    report(table[shown].to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return best


def weekly_panel(close: pd.DataFrame, c: pd.DataFrame, bench: pd.Series) -> pd.DataFrame:
    weeks = close.groupby(close.index.to_period("W-FRI")).apply(lambda g: g.index[-1])
    idx = pd.DatetimeIndex(weeks.values)
    lv = close.loc[idx]
    bench = bench.reindex(close.index).ffill().loc[idx]
    parts = {"C": c.loc[idx], "r12": lv / close.shift(250).loc[idx] - 1, "r3": lv / close.shift(60).loc[idx] - 1}
    for horizon in (8, 13, 26):
        fwd = lv.shift(-horizon) / lv - 1
        parts[f"x{horizon}"] = fwd.sub(fwd.mean(axis=1), axis=0)                    # vs the average industry
        parts[f"h{horizon}"] = fwd.sub(bench.shift(-horizon) / bench - 1, axis=0)   # vs the CSI 300
    panel = pd.concat({k: v.stack(dropna=False) for k, v in parts.items()}, axis=1).reset_index()
    panel.columns = ["week", "industry"] + list(parts)
    return panel.dropna(subset=["C", "r12"])


def stats(group: pd.DataFrame) -> pd.Series:
    out = {"n": len(group)}
    for col in ("x13", "h13", "x26", "h26"):
        values = group[col].dropna()
        out[col] = values.mean()
        out[col + "<0"] = (values < 0).mean()
    return pd.Series(out)


def entries(panel: pd.DataFrame, threshold: float, gap_weeks: int = 13) -> pd.DataFrame:
    rows = []
    for industry, group in panel.sort_values("week").groupby("industry"):
        last = None
        for _, row in group[group["C"] > threshold].iterrows():
            if last is None or (row["week"] - last).days > gap_weeks * 7:
                rows.append(row)
            last = row["week"]
    return pd.DataFrame(rows)


def spell_length(panel: pd.DataFrame, events: pd.DataFrame, exit_level: float) -> pd.Series:
    weeks = []
    for _, event in events.iterrows():
        later = panel[(panel["industry"] == event["industry"]) & (panel["week"] > event["week"])].sort_values("week")
        below = later[later["C"] < exit_level]
        weeks.append((below["week"].iloc[0] - event["week"]).days / 7 if len(below) else np.nan)
    return pd.Series(weeks)


def main() -> None:
    lines: list[str] = []

    def report(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    data = fetch(len(sys.argv) > 1)
    close = data.pivot(index="日期", columns="industry", values="收盘").sort_index()
    amount = data.pivot(index="日期", columns="industry", values="成交额").sort_index()
    report(f"SW L1: {close.shape[1]} indices, {close.index[0].date()} .. {close.index[-1].date()}; "
           f"all 31 present from {close.dropna().index[0].date()}")
    close, amount = close.loc["2014-02-21":], amount.loc["2014-02-21":]
    share = amount.div(amount.sum(axis=1), axis=0)
    cands = candidates(share)
    best = calibrate(close, cands, report)
    panel = weekly_panel(close, cands[best], csi300())
    report(f"\nusing C = {best}; weekly panel {panel['week'].min().date()} .. {panel['week'].max().date()}, "
           f"{len(panel)} industry-weeks")
    report("x = excess over the average industry, h = excess over the CSI 300; <0 = share of cases that underperformed")

    report("\n== C buckets, every industry-week ==")
    buckets = pd.cut(panel["C"], [0, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.2, 99])
    report(panel.groupby(buckets, observed=True).apply(stats).round(3).to_string())

    for threshold in (1.6, 1.8, 2.0):
        events = entries(panel, threshold)
        report(f"\n== first week above C={threshold} (13-week gap between entries): {len(events)} entries, "
               f"{events['industry'].nunique()} industries ==")
        report(stats(events).round(3).to_string())
        if threshold == 1.8:
            events["era"] = np.where(events["week"].dt.year < 2020, "2015-2019", "2020-2026")
            report(events.groupby("era").apply(stats).round(3).to_string())
            hot = events["r12"] > 0.15
            report("  with 12-month return > 15%:\n" + stats(events[hot]).round(3).to_string())
            report("  with 12-month return <= 15%:\n" + stats(events[~hot]).round(3).to_string())
            spell = spell_length(panel, events, 1.4)
            report(f"  weeks until C falls back below 1.4: median {spell.median():.0f}, "
                   f"within 9 weeks {(spell <= 9).mean():.0%}, never {spell.isna().mean():.0%}")
            report(events[["week", "industry", "C", "r12", "x13", "h13", "x26"]].to_string(
                index=False, float_format=lambda v: f"{v:.2f}"))

    report("\n== zone grid: C band x 12-month return ==")
    c_band = pd.cut(panel["C"], [0, 0.8, 1.4, 1.8, 99], labels=["C<0.8", "0.8-1.4", "1.4-1.8", ">1.8"])
    r_band = pd.cut(panel["r12"], [-9, 0.15, 0.5, 99], labels=["r12<15%", "15-50%", ">50%"])
    report(panel.groupby([c_band, r_band], observed=True).apply(stats).round(3).to_string())
    (HERE / "money_map2_report.txt").write_text("\n".join(lines), encoding="utf-8")
    panel.to_parquet(HERE / "money_map2_panel.parquet")


if __name__ == "__main__":
    main()
