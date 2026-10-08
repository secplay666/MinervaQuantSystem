"""How the main strategy (multifactor_rules) changes its holdings at each monthly rebalance, from the orders and
fills of the frozen backtests of 2026-10-07: names kept, sold out, newly bought; kept names trimmed back or topped
up to the equal weight; how long a name stays; whether the names sold out were winners.

    cd ~/L1/minerva-dev && .venv/bin/python ~/L1/research/multifactor/turnover_detail.py \
        artifacts/backtests/<in-sample run> artifacts/backtests/<out-of-sample run>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def rebalances(run: Path) -> pd.DataFrame:
    signals = pd.read_parquet(run / "signals.parquet")
    orders = pd.read_parquet(run / "orders.parquet")
    fills = pd.read_parquet(run / "fills.parquet")
    positions = pd.read_parquet(run / "positions.parquet")
    nav = pd.read_parquet(run / "nav.parquet").set_index("session")
    for frame, column in ((signals, "as_of"), (orders, "session"), (fills, "session"), (positions, "session")):
        frame[column] = pd.to_datetime(frame[column])
    nav.index = pd.to_datetime(nav.index)
    sessions = sorted(positions["session"].unique())
    targets = {d: set(g["symbol"]) for d, g in signals.groupby("as_of")}
    as_of = sorted(targets)
    print(f"  （{run.name}：调仓订单分布在 {orders.loc[orders['reason'] == 'rebalance', 'session'].nunique()} 个交易日，"
          f"信号 {len(as_of)} 次；订单原因 {sorted(orders['reason'].unique())}）")
    rows = []
    for signal_day in as_of:
        day = min((s for s in sessions if s > signal_day), default=None)
        if day is None:
            continue
        before_day = max((s for s in sessions if s < day), default=None)
        held = positions[(positions["session"] == before_day) & (positions["quantity"] > 0)] if before_day is not None \
            else positions.iloc[0:0]
        held_set = set(held["symbol"])
        target = targets[signal_day]
        today = fills[fills["session"] == day]
        sells, buys = today[today["side"] == "sell"], today[today["side"] == "buy"]
        kept = held_set & target
        exits = held_set - target
        value = held.set_index("symbol")
        gains = (value.loc[sorted(exits), "value_fen"] / value.loc[sorted(exits), "cost_fen"] - 1) if exits else pd.Series(dtype=float)
        rows.append({
            "signal": signal_day.date(), "executed": day.date(), "held": len(held_set), "kept": len(kept),
            "sold_out": len(exits), "new": len(target - held_set),
            "trimmed": int(sells["symbol"].isin(kept).sum()), "topped_up": int(buys["symbol"].isin(kept).sum()),
            "untouched": len(kept - set(sells["symbol"]) - set(buys["symbol"])),
            "traded_share": float(today["notional_fen"].sum() / nav.loc[before_day, "nav_fen"]) if before_day is not None else np.nan,
            "exit_gain_share": float((gains > 0).mean()) if len(gains) else np.nan,
            "exit_median_return": float(gains.median()) if len(gains) else np.nan,
        })
    return pd.DataFrame(rows)


def spells(runs: list[Path]) -> pd.Series:
    """Months each name stayed in the target, counted over consecutive monthly signals."""
    lengths = []
    for run in runs:
        signals = pd.read_parquet(run / "signals.parquet")
        months = sorted(signals["as_of"].unique())
        sets = [set(signals.loc[signals["as_of"] == m, "symbol"]) for m in months]
        current: dict[str, int] = {}
        for names in sets:
            for name in list(current):
                if name not in names:
                    lengths.append(current.pop(name))
            for name in names:
                current[name] = current.get(name, 0) + 1
        lengths.extend(current.values())  # still held at the end (censored)
    return pd.Series(lengths)


def main() -> None:
    runs = [Path(p) for p in sys.argv[1:3]]
    for label, run in zip(("样本内 2021–2023", "样本外 2024-01 至 2026-09"), runs):
        table = rebalances(run)
        later = table.iloc[1:]  # the first rebalance only buys
        print(f"\n== {label}：{len(table)} 次调仓（第一次是建仓，不计入平均）")
        print(f"  每次调仓前持有 {later['held'].mean():.0f} 只；继续持有 {later['kept'].mean():.1f} 只，"
              f"清仓 {later['sold_out'].mean():.1f} 只，新买 {later['new'].mean():.1f} 只")
        print(f"  继续持有的里面：减回等权 {later['trimmed'].mean():.1f} 只，加回等权 {later['topped_up'].mean():.1f} 只，"
              f"不动 {later['untouched'].mean():.1f} 只")
        print(f"  每次成交金额约占净值 {later['traded_share'].mean():.0%}（买卖合计）；"
              f"清仓的股票里赚钱的占 {later['exit_gain_share'].mean():.0%}，持有期收益中位数 {later['exit_median_return'].median():+.1%}")
        print(f"  清仓只数：最少 {later['sold_out'].min()}，最多 {later['sold_out'].max()}")
        if label.startswith("样本外"):
            print(table[["signal", "held", "kept", "sold_out", "new", "trimmed", "topped_up", "untouched"]].tail(6).to_string(index=False))
    lengths = spells(runs)
    print(f"\n== 每只股票连续持有几个月（两段合计 {len(lengths)} 段，期末仍持有的按已持有月数计）")
    print(f"  中位数 {lengths.median():.0f} 个月，平均 {lengths.mean():.1f} 个月；"
          f"只持有 1 个月的占 {(lengths == 1).mean():.0%}，持有 6 个月以上的占 {(lengths >= 6).mean():.0%}，"
          f"最长 {lengths.max()} 个月")


if __name__ == "__main__":
    main()
