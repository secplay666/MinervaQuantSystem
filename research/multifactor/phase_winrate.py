"""The main strategy (multifactor_rules) month by month, grouped by the market's phase: how often it rose and how
often it beat the market in each kind of market.  Reads the two frozen backtests of 2026-10-07 (in-sample
2021-2023, out-of-sample 2024-01 to 2026-09); runs nothing new.

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/multifactor/phase_winrate.py \
        data/market.duckdb artifacts/backtests/<in-sample run> artifacts/backtests/<out-of-sample run>

Months run from one month-end close to the next.  Each backtest starts in cash and buys after its first month
end, so the first month of each (2021-01, 2024-01) is left out of the comparison and reported separately.
Phase of a month = CSI 300's four-stage view (steady preset, as the position manager shows an index) on the last
session of the month before: only what was known when the month began.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_system.position.stages import PRESETS, STAGE_KEYS, STAGE_NAMES, classify

STRATEGY, EQUAL, CSI300 = "策略", "等权全收益基准", "H00300（全收益指数）"


def monthly(run: Path) -> pd.DataFrame:
    curves = pd.read_parquet(run / "curves.parquet")
    curves["session"] = pd.to_datetime(curves["session"])
    ends = curves.groupby(curves["session"].dt.to_period("M"))["session"].max()
    level = curves.set_index("session").loc[ends.to_numpy(), [STRATEGY, EQUAL, CSI300]]
    first = curves.iloc[0][[STRATEGY, EQUAL, CSI300]].astype(float)
    returns = level.astype(float).div(pd.concat([first.to_frame().T, level.iloc[:-1]]).to_numpy()) - 1
    returns.index = level.index.to_period("M")
    return returns


def table(frame: pd.DataFrame, key: str, order: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for name, part in frame.groupby(key, sort=False):
        ex_eq, ex_300 = part[STRATEGY] - part[EQUAL], part[STRATEGY] - part[CSI300]
        rows.append({key: name, "月数": len(part),
                     "上涨月占比": (part[STRATEGY] > 0).mean(), "月均收益": part[STRATEGY].mean(),
                     "跑赢等权占比": (ex_eq > 0).mean(), "月均超额（等权）": ex_eq.mean(),
                     "跑赢沪深300占比": (ex_300 > 0).mean(), "月均超额（沪深300）": ex_300.mean(),
                     "沪深300 月均": part[CSI300].mean(), "等权 月均": part[EQUAL].mean()})
    out = pd.DataFrame(rows)
    if order:
        out = out.set_index(key).reindex([o for o in order if o in set(out[key])]).reset_index()
    return out


def show(title: str, frame: pd.DataFrame) -> None:
    print(f"\n== {title}")
    shown = frame.copy()
    for column in shown.columns[2:]:
        shown[column] = shown[column].map(lambda v: f"{v:+.1%}" if "均" in column else f"{v:.0%}")
    print(shown.to_string(index=False))


def main() -> None:
    database, runs = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:4]]
    parts = [monthly(run) for run in runs]
    print("== 起步月（策略还是现金，不计入比较）")
    for part in parts:
        month = part.index[0]
        print(f"  {month}: 策略 {part.iloc[0][STRATEGY]:+.1%}，等权 {part.iloc[0][EQUAL]:+.1%}，"
              f"沪深300 {part.iloc[0][CSI300]:+.1%}")
    oos = parts[1]
    full = (1 + oos).prod() - 1
    rest = (1 + oos.iloc[1:]).prod() - 1
    years_full, years_rest = len(oos) / 12, (len(oos) - 1) / 12
    print("  样本外年化：含起步月 策略 {:.1%} 等权 {:.1%}；去掉起步月 策略 {:.1%} 等权 {:.1%}".format(
        (1 + full[STRATEGY]) ** (1 / years_full) - 1, (1 + full[EQUAL]) ** (1 / years_full) - 1,
        (1 + rest[STRATEGY]) ** (1 / years_rest) - 1, (1 + rest[EQUAL]) ** (1 / years_rest) - 1))
    frame = pd.concat([part.iloc[1:] for part in parts])
    frame["样本"] = ["样本内" if m.year <= 2023 else "样本外" for m in frame.index]

    with duckdb.connect(str(database), read_only=True) as con:
        index = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY 1").fetchdf()
        fin = con.execute("SELECT min(report_date), min(notice_date) FROM fin_income").fetchone()
    index["trade_date"] = pd.to_datetime(index["trade_date"])
    params = PRESETS["steady"][1].for_index()
    stages = classify(index["close"].to_numpy(dtype=float), params)
    index["stage"] = [STAGE_NAMES[int(s)] for s in stages]
    index["month"] = index["trade_date"].dt.to_period("M")
    month_end = index.groupby("month").tail(1).set_index("month")
    before = month_end["stage"].shift(1)  # the stage at the previous month end
    frame["月初沪深300 阶段"] = before.reindex(frame.index).to_numpy()
    move = frame[CSI300]
    frame["当月沪深300"] = np.select([move > 0.03, move < -0.03], ["涨超 3%", "跌超 3%"], "−3% 到 +3%")
    frame["年份"] = [str(m.year) for m in frame.index]

    print(f"\n共 {len(frame)} 个月：{frame.index[0]} 至 {frame.index[-1]}（去掉两个起步月）")
    show("全部", table(frame.assign(全部="全部"), "全部"))
    show("按样本", table(frame, "样本"))
    show("按月初沪深300 所处阶段（仓位管家的四阶段，稳健 200 日）", table(
        frame, "月初沪深300 阶段", [STAGE_NAMES[k] for k in sorted(STAGE_NAMES)]))
    show("按当月沪深300 涨跌", table(frame, "当月沪深300", ["涨超 3%", "−3% 到 +3%", "跌超 3%"]))
    show("按年份", table(frame, "年份"))
    print("\n阶段代码：", {STAGE_KEYS[k]: STAGE_NAMES[k] for k in sorted(STAGE_NAMES)})
    print(f"财报数据最早：报告期 {fin[0]}，公告日 {fin[1]}")
    frame.to_csv(Path(__file__).with_name("phase_winrate_months.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
