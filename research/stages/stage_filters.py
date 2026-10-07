"""Addendum: filters on the base -> advance entries (set C), and an index-specific flat threshold."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from stage_backtest import ADVANCE, BASE, DECLINE, NAMES, ROOT, StageParams, classify, forward_excess
from quant_system.backtest.market_data import load_market_data


def main() -> None:
    market = load_market_data("duckdb", ROOT / "data" / "market.duckdb")
    sessions = pd.to_datetime(pd.Series(market.sessions))
    close = np.where(market.has_bar, market.close / 100.0, np.nan)
    price = pd.DataFrame(close * market.hfq).ffill().to_numpy()
    volume = pd.DataFrame(np.where(market.has_bar, market.volume, np.nan))
    raw_open = np.where(market.has_bar, market.open / 100.0, np.nan)
    listed = market.bar_count >= 250
    with duckdb.connect(str(ROOT / "data" / "market.duckdb"), read_only=True) as con:
        idx = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = 'sh000300' ORDER BY 1").fetchdf()
    idx = idx.set_index(pd.to_datetime(idx["trade_date"]))["close"].reindex(sessions).ffill().to_numpy()[:, None]
    start = int(np.searchsorted(sessions.to_numpy(), np.datetime64("2020-10-01")))
    mask = listed.copy()
    mask[:start] = False
    fwd60 = forward_excess(price, 60)

    p = StageParams("C", ma=200, flat=0.015, confirm=10)
    stage = classify(price, p)
    prev = np.vstack([np.zeros((1, stage.shape[1]), dtype=np.int8), stage[:-1]])
    entries = mask & (prev == BASE) & (stage == ADVANCE)

    # Relative strength: 120-session return above the CSI 300's.
    rs = (price / np.roll(price, 120, axis=0)) > (idx / np.roll(idx, 120, axis=0))
    # Volume expansion at the entry: 20-day mean over the 60-day mean before it.
    v20 = volume.rolling(20, min_periods=15).mean().to_numpy()
    v60 = volume.shift(20).rolling(60, min_periods=40).mean().to_numpy()
    expanding = v20 / v60 >= 1.3
    # The document's criterion 2: volume dried up (10-day mean < 70% of the 20 days before), then within the
    # last 10 sessions an up day with volume >= 2x the 10-day mean excluding that day.
    v10_prior = volume.shift(1).rolling(10, min_periods=8).mean()
    v20_before = volume.shift(11).rolling(20, min_periods=15).mean()
    dried = (v10_prior / v20_before < 0.7).to_numpy()
    surge_day = ((volume >= 2 * v10_prior) & pd.DataFrame(close > raw_open)).to_numpy() & dried
    recent_surge = pd.DataFrame(surge_day.astype(float)).rolling(10, min_periods=1).max().to_numpy() > 0

    index_c = classify(idx, p)[:, 0]
    index_fine = classify(idx, StageParams("idx", ma=200, flat=0.006, confirm=10))[:, 0]
    rows = [
        ("全部筑底→右侧", entries),
        ("+ 相对沪深300走强", entries & rs),
        ("+ 放量（20日均量≥前60日×1.3）", entries & expanding),
        ("+ 文档判据②（缩量后放量阳线）", entries & recent_surge),
        ("+ 走强且放量", entries & rs & expanding),
        ("+ 沪深300非左侧（原阈值）", entries & (index_c != DECLINE)[:, None]),
        ("+ 沪深300非左侧（指数阈值0.6%）", entries & (index_fine != DECLINE)[:, None]),
        ("+ 走强、放量且沪深300非左侧", entries & rs & expanding & (index_fine != DECLINE)[:, None]),
    ]
    print("入场条件                               次数    60日超额  60日绝对   60日跑赢比例")
    for label, sel in rows:
        print(f"{label:<30} {sel.sum():7d}  {np.nanmean(fwd60[1][sel]):+8.2%}  {np.nanmean(fwd60[0][sel]):+8.2%}"
              f"   {np.nanmean(fwd60[1][sel] > 0):6.1%}")
    print("沪深300阶段占比（指数阈值0.6%）按年:")
    years = sessions.dt.year.to_numpy()
    for year in range(2021, 2027):
        sel = years == year
        print(f"  {year}: " + " ".join(f"{NAMES[c][2:]}{np.mean(index_fine[sel] == c):.0%}" for c in NAMES))


if __name__ == "__main__":
    main()
