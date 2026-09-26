"""Extract the small real-data fixture used by the backtest regression test.

The fixture is committed so the golden test never depends on the live store,
which is rebuilt daily.  Re-run this script only on purpose (it changes the
golden outputs; see tests/test_bt_regression.py).

    .venv/Scripts/python.exe scripts/extract_backtest_fixture.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]

# Special cases the engine must handle (see the stage-2 plan).
SPECIAL = {
    "600519": "一手金额超过单只目标（高价股）",
    "002594": "10 转 20 等大比例送转（后复权步长约 3）",
    "603093": "大比例送转 + 派息（2026-08-10）",
    "000908": "重整转增（交易所日历未收录的事件）",
    "000564": "后复权参考价不一致的事件日",
    "600145": "退市整理期（首日不设涨跌幅）后退市",
    "600485": "长期停牌后退市",
    "600837": "并购退市（海通证券）",
    "601989": "并购退市（中国重工）",
    "000004": "2026 年退市",
    "000792": "长期停牌后复牌",
    "300750": "创业板 2020-08-24 涨跌幅改制",
    "688981": "科创板 200 股起、1 股递增",
    "600112": "上交所 ST 期间一字 ±5% 锁板（公告推导的 ST 历史）",
    "600289": "上交所 ST 期间一字 ±5% 锁板（公告推导的 ST 历史）",
    "002052": "深市 ST 期间一字 ±5% 锁板",
    "000609": "深市 ST 期间一字 ±5% 锁板",
    "000838": "深市 2026 年被实施 *ST",
}
LIQUID_COUNT = 22
INDICES = ("sh000300", "sh000905")


def extract(database: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database), read_only=True) as con:
        special = sorted(SPECIAL)
        liquid = [row[0] for row in con.execute(
            f"""
            SELECT b.symbol FROM daily_bars b JOIN security_master m USING (symbol)
            WHERE m.board IN ('SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR')
              AND b.symbol NOT IN ({",".join(f"'{s}'" for s in special)})
            GROUP BY b.symbol HAVING min(b.trade_date) <= DATE '2020-01-31'
            ORDER BY avg(b.turnover_cny) DESC, b.symbol LIMIT {LIQUID_COUNT}
            """
        ).fetchall()]
        symbols = sorted(set(special) | set(liquid))
        in_list = ",".join(f"'{s}'" for s in symbols)
        queries = {
            "calendar": "SELECT trade_date FROM trading_calendar ORDER BY trade_date",
            "bars": f"""SELECT symbol, trade_date, open, high, low, close, volume_shares, turnover_cny
                        FROM daily_bars WHERE symbol IN ({in_list}) ORDER BY symbol, trade_date""",
            "factors": f"""SELECT symbol, effective_date, hfq_factor FROM adjustment_factors
                           WHERE symbol IN ({in_list}) ORDER BY symbol, effective_date""",
            "master": f"""SELECT symbol, board, exchange, list_date, delist_date FROM security_master
                          WHERE symbol IN ({in_list}) ORDER BY symbol""",
            "risk": f"""SELECT symbol, status, start_date, end_date, method, source FROM risk_warning_intervals
                        WHERE symbol IN ({in_list}) ORDER BY symbol, start_date""",
            "indices": f"""SELECT symbol, trade_date, close FROM index_bars
                           WHERE symbol IN ({",".join(f"'{s}'" for s in INDICES)}) ORDER BY symbol, trade_date""",
        }
        counts = {}
        for name, sql in queries.items():
            frame = con.execute(sql).fetchdf()
            frame.to_parquet(output / f"{name}.parquet", index=False, compression="zstd")
            counts[name] = len(frame)
        version = con.execute(
            "SELECT run_id, data_version FROM ingestion_runs WHERE status = 'complete' "
            "AND data_version IS NOT NULL ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    source = {
        "description": "Backtest regression fixture extracted from the canonical store.",
        "catalog_run_id": version[0] if version else None,
        "data_version": version[1] if version else None,
        "symbols": symbols,
        "special_cases": SPECIAL,
        "liquid_selection": f"top {LIQUID_COUNT} by average daily turnover, listed by 2020-01-31",
        "row_counts": counts,
        "script": "scripts/extract_backtest_fixture.py",
    }
    (output / "SOURCE.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(ROOT / "data" / "market.duckdb"))
    parser.add_argument("--output", default=str(ROOT / "tests" / "fixtures" / "backtest_cn_small"))
    args = parser.parse_args()
    source = extract(Path(args.database), Path(args.output))
    print(json.dumps({"symbols": len(source["symbols"]), "rows": source["row_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
