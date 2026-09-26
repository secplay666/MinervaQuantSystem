"""Extract the real-data fixture for the multi-factor regression test.

~150 symbols spread evenly over the liquidity ranking of stocks listed by
mid-2019, plus names delisted during the sample (survivorship), with their
bars, factors, master rows, risk intervals, indices, share capital,
industry, dividends and financial statements.  Committed so the golden test
never depends on the live store.  Re-run only on purpose (it changes the
golden outputs; see tests/test_mf_regression.py):

    .venv/Scripts/python.exe scripts/extract_multifactor_fixture.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np

from quant_system.fundamentals.pit import FIN_COLUMNS, INPUTS
from quant_system.research.data import RESEARCH_QUERIES

ROOT = Path(__file__).resolve().parents[1]
SPREAD_COUNT = 140
DELISTED_COUNT = 10
INDICES = ("H00300", "H00905", "H00852", "sh000300")


def _filtered(sql: str, in_list: str) -> str:
    """Add a symbol filter to a RESEARCH_QUERIES statement."""
    head, _, order = sql.partition("ORDER BY")
    clause = "AND" if "WHERE" in head else "WHERE"
    return f"{head} {clause} symbol IN ({in_list}) ORDER BY {order}"


def extract(database: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database), read_only=True) as con:
        ranked = [row[0] for row in con.execute(
            """
            SELECT b.symbol FROM daily_bars b JOIN security_master m USING (symbol)
            WHERE m.board IN ('SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR') AND m.list_date <= DATE '2019-06-30'
              AND b.trade_date BETWEEN DATE '2020-01-01' AND DATE '2023-12-31'
            GROUP BY b.symbol HAVING count(*) > 500
            ORDER BY avg(b.turnover_cny) DESC, b.symbol
            """).fetchall()]
        picks = sorted({ranked[int(i)] for i in np.linspace(0, len(ranked) - 1, SPREAD_COUNT)})
        delisted = [row[0] for row in con.execute(
            f"""
            SELECT symbol FROM security_master
            WHERE delist_date BETWEEN DATE '2021-06-01' AND DATE '2025-12-31'
              AND board IN ('SSE_MAIN', 'SZSE_MAIN', 'CHINEXT', 'STAR') AND list_date <= DATE '2019-06-30'
            ORDER BY symbol LIMIT {DELISTED_COUNT}
            """).fetchall()]
        symbols = sorted(set(picks) | set(delisted))
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
        queries.update({name: _filtered(sql, in_list) for name, sql in RESEARCH_QUERIES.items()})
        for statement, fields in INPUTS.items():
            columns = ", ".join(FIN_COLUMNS + list(fields))
            queries[f"fin_{statement}"] = (f"SELECT {columns} FROM fin_{statement} WHERE symbol IN ({in_list}) "
                                           "ORDER BY symbol, report_date, version")
        counts = {}
        for name, sql in queries.items():
            frame = con.execute(sql).fetchdf()
            frame.to_parquet(output / f"{name}.parquet", index=False, compression="zstd")
            counts[name] = len(frame)
        version = con.execute(
            "SELECT run_id, data_version FROM ingestion_runs WHERE status = 'complete' "
            "AND data_version IS NOT NULL ORDER BY run_id DESC LIMIT 1").fetchone()
    source = {
        "description": "Multi-factor regression fixture extracted from the canonical store.",
        "catalog_run_id": version[0] if version else None,
        "data_version": version[1] if version else None,
        "selection": f"{SPREAD_COUNT} names evenly spaced over the 2020-2023 liquidity ranking of stocks listed "
                     f"by 2019-06-30, plus {DELISTED_COUNT} delisted between 2021-06 and 2025",
        "symbols": symbols,
        "row_counts": counts,
        "script": "scripts/extract_multifactor_fixture.py",
    }
    (output / "SOURCE.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(ROOT / "data" / "market.duckdb"))
    parser.add_argument("--output", default=str(ROOT / "tests" / "fixtures" / "multifactor_cn"))
    args = parser.parse_args()
    source = extract(Path(args.database), Path(args.output))
    print(json.dumps({"symbols": len(source["symbols"]), "rows": source["row_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
