from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import duckdb
import pandas as pd

from .audit import run_audit
from .config import DataPlatformConfig
from .pipeline import STEPS, IngestionPipeline
from .storage import CatalogError, build_duckdb_catalog, read_canonical


def _root_from_file() -> Path:
    return Path(__file__).resolve().parents[3]


def _configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load_config(args: argparse.Namespace) -> DataPlatformConfig:
    return DataPlatformConfig.load(Path(args.config).resolve())


def command_ingest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    steps = set(args.steps.split(",")) if args.steps else None
    manifest = IngestionPipeline(root, _load_config(args)).run(mode=args.mode, steps=steps)
    _print_json({key: manifest.get(key) for key in (
        "run_id", "status", "mode", "expected_latest_date", "quality_summary", "counters",
        "data_version", "catalog_status", "quality_report_markdown", "fatal_error")})
    return 0 if manifest["status"] == "complete" else 2


def command_rebuild(args: argparse.Namespace) -> int:
    from .rebuild import rebuild_canonical

    report = rebuild_canonical(Path(args.root).resolve(), _load_config(args), apply=args.apply)
    _print_json({key: report.get(key) for key in (
        "run_id", "status", "applied", "staging", "archived_to", "counters", "quality_summary",
        "diff", "audit")})
    return 0 if report["status"] == "complete" else 2


def command_audit(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = _load_config(args)
    calendar = read_canonical(root, "trading_calendar")
    if calendar is None or calendar.empty:
        print("No canonical trading calendar; run ingest first.", file=sys.stderr)
        return 1
    result = run_audit(root, max(calendar["trade_date"]), pd.to_datetime(config.start_date).date(),
                       config.min_latest_coverage, "adhoc_audit")
    _print_json({"summary": result.summary, "issues": [issue.to_dict() for issue in result.issues]})
    return 2 if any(issue.severity == "blocking" for issue in result.issues) else 0


def command_catalog(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if args.rebuild:
        try:
            print(f"Catalog rebuilt: {build_duckdb_catalog(root)}")
        except CatalogError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    database = root / "data" / "market.duckdb"
    if not database.exists():
        print(f"Database does not exist: {database}", file=sys.stderr)
        return 1
    with duckdb.connect(str(database), read_only=True) as con:
        rows = con.execute("SELECT * FROM data_catalog ORDER BY dataset").fetchdf()
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
    ingest.add_argument(
        "--mode",
        choices=("full", "incremental"),
        default="incremental",
        help="full replaces each selected history; incremental resumes from local partitions",
    )
    ingest.add_argument(
        "--steps",
        help=f"comma-separated subset of: {','.join(STEPS)} "
             "(calendar, security master and the audit always run)",
    )
    ingest.set_defaults(handler=command_ingest)

    rebuild = subparsers.add_parser(
        "rebuild", help="regenerate the canonical layer from raw data (dry run unless --apply)"
    )
    rebuild.add_argument("--config", default="configs/data_platform.json")
    rebuild.add_argument("--apply", action="store_true",
                         help="archive the live canonical layer and swap the rebuilt one in")
    rebuild.set_defaults(handler=command_rebuild)

    audit = subparsers.add_parser("audit", help="run whole-dataset quality checks")
    audit.add_argument("--config", default="configs/data_platform.json")
    audit.set_defaults(handler=command_audit)

    catalog = subparsers.add_parser("catalog", help="show available datasets")
    catalog.add_argument("--rebuild", action="store_true", help="rebuild market.duckdb from canonical parquet")
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
