"""What simple market-exposure rules would have done to the main strategy (multifactor_rules), month by month:
half the money in cash when the market looks weak at the start of a month.  A quick look on the months of
phase_winrate.py (2021-02 to 2026-09), not a backtest: cash earns nothing, switching costs are ignored, and only
month-end drawdowns are seen.

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/multifactor/exposure_overlays.py \
        data/market.duckdb ~/L1/research/multifactor/phase_winrate_months.csv

Every rule only uses what was known at the previous month end.  Trying many rules on 67 months and keeping the
best one would be overfitting; this shows the trade-off, it does not pick a rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from quant_system.app.breadth import MarketBreadth

STRATEGY = "策略"


def stats(returns: pd.Series, exposure: pd.Series) -> dict[str, float]:
    curve = (1 + returns).cumprod()
    years = len(returns) / 12
    drawdown = curve / curve.cummax().clip(lower=1) - 1
    return {"年化": curve.iloc[-1] ** (1 / years) - 1, "最大回撤（月末）": drawdown.min(), "最差月": returns.min(),
            "平均仓位": exposure.mean(), "半仓月数": int((exposure < 1).sum())}


def main() -> None:
    database, months_csv = Path(sys.argv[1]), Path(sys.argv[2])
    frame = pd.read_csv(months_csv, encoding="utf-8-sig", index_col=0)
    frame.index = pd.PeriodIndex(frame.index, freq="M")
    base = frame[STRATEGY]

    series = pd.DataFrame(MarketBreadth(database).overview()["series"])
    series["month"] = pd.to_datetime(series["trade_date"]).dt.to_period("M")
    breadth = series.groupby("month").tail(1).set_index("month")[["above60", "above250"]].shift(1)  # previous month end
    breadth = breadth.reindex(frame.index)

    curve = (1 + base).cumprod()
    drawdown_before = (curve / curve.cummax().clip(lower=1) - 1).shift(1).fillna(0)  # the strategy's own, at month start

    stage = frame["月初沪深300 阶段"]
    rules = {
        "一直满仓（现在的做法）": pd.Series(1.0, index=frame.index),
        "沪深300 左侧或顶部时半仓": stage.isin(["左侧", "顶部"]).map({True: 0.5, False: 1.0}),
        "沪深300 不在右侧就半仓": (stage != "右侧").map({True: 0.5, False: 1.0}),
        "站上 250 日线的股票 < 50% 时半仓": (breadth["above250"] < 0.5).map({True: 0.5, False: 1.0}),
        "站上 60 日线的股票 < 50% 时半仓": (breadth["above60"] < 0.5).map({True: 0.5, False: 1.0}),
        "账户从高点回撤 > 10% 时半仓": (drawdown_before < -0.10).map({True: 0.5, False: 1.0}),
    }
    rows = []
    for name, exposure in rules.items():
        rows.append({"规则": name, **stats(base * exposure, exposure)})
    out = pd.DataFrame(rows)
    for column in ("年化", "最大回撤（月末）", "最差月", "平均仓位"):
        out[column] = out[column].map(lambda v: f"{v:+.1%}" if column != "平均仓位" else f"{v:.0%}")
    print(f"{frame.index[0]} 至 {frame.index[-1]}，{len(frame)} 个月；现金收益按 0 计，不计调仓成本")
    print(out.to_string(index=False))

    print("\n各规则半仓的月份里，策略本身的表现（满仓时）：")
    for name, exposure in list(rules.items())[1:]:
        half = base[exposure < 1]
        if len(half):
            print(f"  {name}: {len(half)} 个月，满仓月均 {half.mean():+.1%}，其中赚钱的月份 {(half > 0).mean():.0%}")


if __name__ == "__main__":
    main()
