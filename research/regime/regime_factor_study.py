"""Do the factor families of the main strategy work differently in different market regimes, and would
switching family weights by the regime (known at the time) have helped?  Question of 2026-10-08.

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/regime/regime_factor_study.py \
        data/market.duckdb <factor evaluation dirs, oldest first>

Inputs are factor evaluations (``quant-research factors evaluate``): per factor and month-end signal, the
top-quintile return minus the universe's equal-weight return over the next month (bought at the next open).
A family's monthly excess is the mean of its members'.  Value, quality and growth exist from about 2017 on
(financial statements start with 2016 Q1); before that the composite is the five price-volume families.

Regimes at a signal date use the CSI 300 price index up to that close only:
  stage   the position manager's four-stage view (steady 200-day preset, index variant)
  ma200   close above or below its 200-session average
  mom12   return over the last 250 sessions above or below 0
  vol     60-session realized volatility above or below its median over all earlier sessions
  bull/bear (description only): 20% moves between alternating peaks and troughs, dated after the fact.

Rules (fixed before looking at the results), each month t:
  W0  equal weight on the families that have data (the strategy as it is)
  R-* families whose mean excess in the current regime state was positive in months up to t-2
      (a month's result is fully known only after the next rebalance), equal weight; at least 12 months in
      that state, otherwise all earlier months; none positive -> W0
  M12 families with a positive mean over months t-13..t-2 (factor momentum, no regime)
  EXP families with a positive mean over all months up to t-2 (learned once, no regime)
The monthly result of a rule is the weighted family excess: a proxy for the composite portfolio, which mixes
signals within each stock; it ranks rules, it is not a backtest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_system.features.registry import all_factors
from quant_system.position.stages import PRESETS, STAGE_NAMES, classify

FAMILY_NAMES = {"value": "价值", "quality": "质量", "growth": "成长", "momentum": "动量", "volatility": "波动",
                "liquidity": "流动性", "size": "规模", "technical": "技术"}
MIN_STATE_MONTHS = 12
BURN_IN_END = "2010-12-31"
TEST_START = "2021-01-01"


def load_months(dirs: list[Path]) -> pd.DataFrame:
    family = {spec.id: spec.family for spec in all_factors()}
    frames = []
    for directory in dirs:
        q = pd.read_parquet(directory / "quantiles.parquet")[["factor", "session", "top_minus_universe"]]
        frames.append(q)
    q = pd.concat(frames).drop_duplicates(["factor", "session"], keep="last")
    q["session"] = pd.to_datetime(q["session"])
    q["family"] = q["factor"].map(family)
    by_family = q.groupby(["session", "family"])["top_minus_universe"].mean().unstack("family")
    by_factor = q.pivot(index="session", columns="factor", values="top_minus_universe")
    return by_family.sort_index(), by_factor.sort_index()


def regimes(database: Path, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    with duckdb.connect(str(database), read_only=True) as con:
        index = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY 1").fetchdf()
    index["trade_date"] = pd.to_datetime(index["trade_date"])
    close = index.set_index("trade_date")["close"].astype(float)
    stage = pd.Series(classify(close.to_numpy(), PRESETS["steady"][1].for_index()), index=close.index)
    ma200 = close.rolling(200, min_periods=200).mean()
    mom12 = close / close.shift(250) - 1
    vol60 = np.log(close).diff().rolling(60, min_periods=60).std() * np.sqrt(244)
    vol_median = vol60.expanding(min_periods=250).median().shift(1)
    out = pd.DataFrame(index=sessions)
    at = close.index.get_indexer(sessions, method="pad")
    out["stage"] = [STAGE_NAMES[int(stage.iloc[i])] for i in at]
    out["ma200"] = np.where(close.iloc[at].to_numpy() > ma200.iloc[at].to_numpy(), "200 日线上方", "200 日线下方")
    out.loc[np.isnan(ma200.iloc[at].to_numpy()), "ma200"] = None
    m = mom12.iloc[at].to_numpy()
    out["mom12"] = np.where(m > 0, "过去一年上涨", "过去一年下跌")
    out.loc[np.isnan(m), "mom12"] = None
    v, med = vol60.iloc[at].to_numpy(), vol_median.iloc[at].to_numpy()
    out["vol"] = np.where(v > med, "高波动", "低波动")
    out.loc[np.isnan(v) | np.isnan(med), "vol"] = None
    phase = bull_bear(close)
    # the forward month (signal close to the next signal close) is labelled by the phase most of its days are in
    ends = list(sessions[1:]) + [close.index[-1]]
    out["bull_bear"] = [phase.loc[(phase.index > s) & (phase.index <= e)].mode().iloc[0]
                        if ((phase.index > s) & (phase.index <= e)).any() else None for s, e in zip(sessions, ends)]
    out["index_next"] = [close.asof(e) / close.asof(s) - 1 for s, e in zip(sessions, ends)]
    return out


def bull_bear(close: pd.Series, threshold: float = 0.20) -> pd.Series:
    """Alternating peaks and troughs at least ``threshold`` apart; days from a trough to the next peak are 牛市."""
    turns: list[tuple[int, str]] = []  # (position, "peak" | "trough")
    values = close.to_numpy()
    hi = lo = 0
    state = None
    for i in range(1, len(values)):
        if values[i] > values[hi]:
            hi = i
        if values[i] < values[lo]:
            lo = i
        if state != "bear" and values[i] <= values[hi] * (1 - threshold):
            turns.append((hi, "peak"))
            state, lo = "bear", i
        elif state != "bull" and values[i] >= values[lo] * (1 + threshold):
            turns.append((lo, "trough"))
            state, hi = "bull", i
    label = pd.Series(None, index=close.index, dtype=object)
    for (start, kind), (end, _) in zip(turns, turns[1:] + [(len(values) - 1, "")]):
        label.iloc[start:end + 1] = "熊市" if kind == "peak" else "牛市"
    return label


def describe(fam: pd.DataFrame, reg: pd.DataFrame, key: str, window: slice) -> pd.DataFrame:
    part = fam.loc[window].join(reg[key]).dropna(subset=[key])
    rows = []
    for state, group in part.groupby(key):
        row = {key: state, "月数": len(group)}
        for f in FAMILY_NAMES:
            if f in group and group[f].notna().sum() >= 6:
                row[FAMILY_NAMES[f]] = group[f].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def walk_forward(fam: pd.DataFrame, reg: pd.DataFrame) -> pd.DataFrame:
    sessions = fam.index
    out = {}
    rules = ["W0", "R-stage", "R-ma200", "R-mom12", "R-vol", "M12", "EXP"]
    for rule in rules:
        values = []
        for k, t in enumerate(sessions):
            present = [f for f in fam.columns if np.isfinite(fam.at[t, f])]
            if not present:
                values.append(np.nan)
                continue
            known = fam.iloc[:max(k - 1, 0)]  # months up to t-2
            chosen = present
            if rule != "W0" and len(known) >= MIN_STATE_MONTHS:
                if rule.startswith("R-"):
                    key = rule[2:]
                    state = reg.at[t, key]
                    states = reg[key].iloc[:max(k - 1, 0)]
                    in_state = known[states.to_numpy() == state] if state is not None else known.iloc[0:0]
                    base = in_state if len(in_state) >= MIN_STATE_MONTHS else known
                elif rule == "M12":
                    base = known.iloc[-12:]
                else:
                    base = known
                means = base[present].mean()
                positive = [f for f in present if np.isfinite(means[f]) and means[f] > 0]
                chosen = positive or present
            values.append(float(fam.loc[t, chosen].mean()))
        out[rule] = values
    return pd.DataFrame(out, index=sessions)


def score(results: pd.DataFrame, window: slice) -> pd.DataFrame:
    part = results.loc[window].dropna()
    rows = []
    for rule in part.columns:
        r = part[rule]
        diff = r - part["W0"]
        curve = (1 + r).cumprod()
        rows.append({"规则": rule, "月数": len(r), "月均超额": r.mean(), "年化": (1 + r.mean()) ** 12 - 1,
                     "t": r.mean() / r.std(ddof=1) * np.sqrt(len(r)), "正的月份": (r > 0).mean(),
                     "最大回撤": (curve / curve.cummax() - 1).min(),
                     "比现在多（月均）": diff.mean(),
                     "差的 t": diff.mean() / diff.std(ddof=1) * np.sqrt(len(r)) if diff.std(ddof=1) > 0 else np.nan})
    return pd.DataFrame(rows)


def pct(frame: pd.DataFrame, skip: tuple[str, ...]) -> pd.DataFrame:
    shown = frame.copy()
    for column in shown.columns:
        if column in skip or shown[column].dtype == object:
            continue
        if column in ("t", "差的 t"):
            shown[column] = shown[column].map(lambda v: f"{v:+.2f}")
        else:
            shown[column] = shown[column].map(lambda v: "" if pd.isna(v) else f"{v:+.2%}")
    return shown


def main() -> None:
    database, dirs = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    fam, by_factor = load_months(dirs)
    reg = regimes(database, fam.index)
    print(f"月份：{fam.index[0]:%Y-%m} 至 {fam.index[-1]:%Y-%m}，共 {len(fam)} 个；"
          f"各类因子有数据的月数：{ {FAMILY_NAMES[f]: int(fam[f].notna().sum()) for f in fam.columns} }")
    train, test = slice(None, "2020-12-31"), slice(TEST_START, None)

    print("\n== 事后划分的牛熊（沪深300 涨跌 20% 为界）")
    phases = reg["bull_bear"].fillna("未定")
    print("  " + "；".join(f"{p} {n} 个月" for p, n in phases.value_counts().items()))
    for label, window in (("2006–2020", train), ("2021–2026", test)):
        print(f"\n== 各类因子每月超额（前 1/5 减股票池平均），按事后牛熊，{label}")
        print(pct(describe(fam, reg, "bull_bear", window), ("bull_bear", "月数")).to_string(index=False))
    for key, title in (("stage", "月初沪深300 四阶段"), ("ma200", "沪深300 与 200 日线"),
                       ("mom12", "沪深300 过去一年涨跌"), ("vol", "沪深300 波动高低")):
        for label, window in (("2006–2020", train), ("2021–2026", test)):
            print(f"\n== 各类因子每月超额，按{title}（月底可知），{label}")
            print(pct(describe(fam, reg, key, window), (key, "月数")).to_string(index=False))

    reg["当月沪深300"] = np.select([reg["index_next"] > 0.03, reg["index_next"] < -0.03], ["涨超 3%", "跌超 3%"],
                                 "±3% 以内")
    for label, window in (("2006–2020", train), ("2021–2026", test)):
        print(f"\n== 各类因子每月超额，按当月沪深300 涨跌（事后），{label}")
        print(pct(describe(fam, reg, "当月沪深300", window), ("当月沪深300", "月数")).to_string(index=False))

    results = walk_forward(fam, reg)
    for label, window in (("2011–2020（滚动检验）", slice("2011-01-01", "2020-12-31")),
                          ("2021–2026（最终检验）", test)):
        print(f"\n== 按规则调整因子类别后的每月超额（代理，非回测），{label}")
        print(pct(score(results, window), ("规则", "月数")).to_string(index=False))
    out = fam.rename(columns=FAMILY_NAMES).join(reg).join(results)
    out.to_csv(Path(__file__).with_name("regime_months.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
