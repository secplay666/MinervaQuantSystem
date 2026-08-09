from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import duckdb

from .config import DataPlatformConfig
from .pipeline import IngestionPipeline


def _root_from_file() -> Path:
    return Path(__file__).resolve().parents[3]


def _configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def command_ingest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = DataPlatformConfig.load(Path(args.config).resolve())
    manifest = IngestionPipeline(root, config).run()
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return 0 if manifest["status"] == "complete" else 2


def command_catalog(args: argparse.Namespace) -> int:
    database = Path(args.root).resolve() / "data" / "market.duckdb"
    if not database.exists():
        print(f"Database does not exist: {database}", file=sys.stderr)
        return 1
    with duckdb.connect(str(database), read_only=True) as con:
        rows = con.execute(
            "SELECT * FROM data_catalog ORDER BY dataset"
        ).fetchdf()
    print(rows.to_string(index=False))
    return 0


def command_query(args: argparse.Namespace) -> int:
    sql = args.sql.strip()
    if not sql.lower().startswith(("select", "with", "describe", "show")):
        print("Only read-only SQL is accepted.", file=sys.stderr)
        return 1
    database = Path(args.root).resolve() / "data" / "market.duckdb"
    with duckdb.connect(str(database), read_only=True) as con:
        result = con.execute(sql).fetchdf()
    print(result.to_string(index=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quant data platform CLI")
    parser.add_argument("--root", default=str(_root_from_file()))
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="download and normalize data")
    ingest.add_argument("--config", default="configs/data_platform.json")
    ingest.set_defaults(handler=command_ingest)

    catalog = subparsers.add_parser("catalog", help="show available datasets")
    catalog.set_defaults(handler=command_catalog)

    query = subparsers.add_parser("query", help="run a read-only DuckDB query")
    query.add_argument("sql")
    query.set_defaults(handler=command_query)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()

