"""Stage backtest of the three presets over the whole history (the 阶段回测 page's numbers, 2005 onward).

Run from a checkout whose data/market.duckdb holds the 2005 backfill (the dev env):
    .venv/bin/python research/stages/stage_backtest_full.py OUT_DIR
Writes OUT_DIR/stage_backtest_<preset>.json and prints a summary: the stage-2 book against the market
per year with the CSI 300's stage mix, the stages' forward excess returns, and base->advance entries by
the index's stage.  Memory: the whole market is loaded once (about 8-10 GB).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

from quant_system.app.market import MarketQueries
from quant_system.app.position import load_series
from quant_system.backtest.market_data import load_market_data
from quant_system.position.stage_backtest import run_stage_backtest
from quant_system.position.stages import PRESETS


def pct(value) -> str:
    return "  —  " if value is None else f"{value:+.1%}"


def main() -> None:
    root = Path.cwd()
    database = root / "data" / "market.duckdb"
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    market = MarketQueries(database, root / "configs" / "market_rules" / "cn_a_share.json")
    data = load_market_data("duckdb", database)
    index = load_series(market, "sh000300", "index")
    closes = pd.Series(index.close, index=pd.to_datetime(index.dates))
    aligned = closes.reindex(pd.to_datetime(list(data.sessions))).to_numpy(dtype=float)
    print(f"loaded {len(data.sessions)} sessions in {time.time() - started:.0f}s", flush=True)
    for key, (name, params) in PRESETS.items():
        started = time.time()
        result = run_stage_backtest(data, aligned, params)
        (out / f"stage_backtest_{key}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                                                        encoding="utf-8")
        overall = result["overall"]
        print(f"\n=== {name}: {result['start']} .. {result['end']}, {result['stocks']} stocks, "
              f"{time.time() - started:.0f}s", flush=True)
        for book in ("market", "stage2", "stage2_gated"):
            stats = overall[book]
            names = f", avg {stats['avg_names']:.0f} names" if "avg_names" in stats else ""
            print(f"  {book:13s} total {pct(stats['total'])}  max drawdown {pct(stats['max_drawdown'])}{names}")
        print("  stage        share  excess20 excess60 excess120 beat60")
        for row in result["stages"]:
            print(f"  {row['name']:6s} {row['share']:8.1%} {pct(row['excess_20']):>8} {pct(row['excess_60']):>8} "
                  f"{pct(row['excess_120']):>9} {row['beat_60'] or 0:6.0%}")
        print("  base->advance entries by CSI 300 stage (count, excess60, beat60):")
        for row in result["gate"] + result["filters"]:
            print(f"    {row['name']:22s} {row['count']:7d} {pct(row['excess_60']):>8} {row['beat_60'] or 0:5.0%}")
        print("  year   market  stage2  gated   CSI300 stages (base/advance/top/decline)")
        for row in result["yearly"]:
            mix = "/".join(f"{row['index_stages'][k]:.0%}" for k in ("base", "advance", "top", "decline"))
            print(f"  {row['year']}  {pct(row['market']):>7} {pct(row['stage2']):>7} {pct(row['stage2_gated']):>7}   {mix}")


if __name__ == "__main__":
    main()
