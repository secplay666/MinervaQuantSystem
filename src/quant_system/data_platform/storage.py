from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .utils import file_sha256, json_hash

RAW_SOURCE_KEY = b"quant_system.source"

# Canonical datasets materialized into DuckDB, with their layout.
PARTITIONED_DATASETS = ("daily_bars", "adjustment_factors", "index_bars", "market_snapshot", "etf_bars",
                        "intraday_bars", "intraday_trades", "sw_index_bars")
SINGLE_FILE_DATASETS = (
    "security_master",
    "trading_calendar",
    "suspension_events",
    "security_name_changes",
    "risk_warning_intervals",
    "risk_warning_bulletins",
    "risk_warning_adjustments",
    "bar_gaps",
    "share_capital",
    "dividends",
    "industry_sw",
    "index_weights",
    "fin_income",
    "fin_balance",
    "fin_cashflow",
    "earnings_forecast",
    "etf_shares",
    "etf_master",
    "etf_top_holders",
    "buybacks",
    "holder_changes",
)
DATE_COLUMNS = {
    "daily_bars": "trade_date",
    "adjustment_factors": "effective_date",
    "index_bars": "trade_date",
    "market_snapshot": "snapshot_date",
    "trading_calendar": "trade_date",
    "suspension_events": "suspend_start",
    "security_name_changes": "effective_date",
    "risk_warning_bulletins": "pub_date",
    "share_capital": "change_date",
    "dividends": "report_date",
    "industry_sw": "start_date",
    "index_weights": "as_of_date",
    "fin_income": "report_date",
    "fin_balance": "report_date",
    "fin_cashflow": "report_date",
    "earnings_forecast": "notice_date",
    "etf_shares": "trade_date",
    "etf_bars": "trade_date",
    "intraday_bars": "trade_date",
    "intraday_trades": "trade_date",
    "etf_top_holders": "report_date",
    "sw_index_bars": "trade_date",
    "buybacks": "notice_date",
    "holder_changes": "notice_date",
}


class CatalogError(RuntimeError):
    pass


# Most vendor suspension records (the Baidu calendar) carry no end date.  The
# catalog derives it from the bars: the suspension ends on the last session
# before the first bar after its start.  Derived at every build, so an event
# gets its end once the stock trades again; the canonical records stay as
# delivered.  ``end_date`` is the vendor's end when there is one.
SUSPENSION_SPANS_SQL = """
CREATE TABLE suspension_spans AS
WITH events AS (
    SELECT symbol, suspend_start, suspend_end, expected_resume, suspension_type, reason, source
    FROM suspension_events WHERE suspend_start IS NOT NULL
),
resumed AS (
    SELECT e.symbol, e.suspend_start, min(b.trade_date) AS resume_date
    FROM events e JOIN daily_bars b ON b.symbol = e.symbol AND b.trade_date > e.suspend_start
    GROUP BY 1, 2
)
SELECT e.*, r.resume_date,
       CASE WHEN e.suspend_end IS NOT NULL THEN CAST(e.suspend_end AS DATE)
            WHEN r.resume_date IS NULL THEN NULL
            ELSE greatest(CAST(e.suspend_start AS DATE),
                          (SELECT max(c.trade_date) FROM trading_calendar c WHERE c.trade_date < r.resume_date))
       END AS end_date,
       e.suspend_end IS NULL AND r.resume_date IS NOT NULL AS end_inferred
FROM events e LEFT JOIN resumed r USING (symbol, suspend_start)
ORDER BY symbol, suspend_start
"""


def write_parquet_atomic(
    frame: pd.DataFrame, path: Path, metadata: dict[bytes, bytes] | None = None
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if metadata:
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    pq.write_table(table, temp_path, compression="zstd")
    temp_path.replace(path)
    return path


def write_raw_frame(
    root: Path,
    provider: str,
    dataset: str,
    run_id: str,
    name: str,
    frame: pd.DataFrame,
    source: str | None = None,
) -> Path:
    """Persist a vendor response unchanged; ``source`` is kept in file metadata.

    JSON APIs can mix types within a field (e.g. numbers and text); such
    object columns are stored as text rather than failing the run.
    """
    metadata = {RAW_SOURCE_KEY: source.encode("utf-8")} if source else None
    path = root / "data" / "raw" / provider / dataset / f"run_id={run_id}" / f"{name}.parquet"
    try:
        return write_parquet_atomic(frame, path, metadata)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError):
        text = frame.copy()
        for column in text.columns[text.dtypes == object]:
            text[column] = text[column].map(lambda value: value if value is None or isinstance(value, str)
                                            else str(value))
        return write_parquet_atomic(text, path, metadata)


def read_raw_source(path: Path) -> str | None:
    metadata = pq.read_schema(path).metadata or {}
    value = metadata.get(RAW_SOURCE_KEY)
    return value.decode("utf-8") if value else None


def canonical_path(root: Path, dataset: str, partition: str | None = None) -> Path:
    directory = root / "data" / "canonical" / dataset
    if partition:
        directory /= partition
    return directory / "data.parquet"


def write_canonical_frame(
    root: Path, dataset: str, frame: pd.DataFrame, partition: str | None = None
) -> Path:
    if frame.empty or len(frame.columns) == 0:
        raise ValueError(f"Refusing to overwrite canonical {dataset}/{partition or ''} with an empty frame")
    return write_parquet_atomic(frame, canonical_path(root, dataset, partition))


def read_canonical(root: Path, dataset: str, partition: str | None = None) -> pd.DataFrame | None:
    path = canonical_path(root, dataset, partition)
    return pd.read_parquet(path) if path.exists() else None


# ---------------------------------------------------------------------------
# Inventory / data version
# ---------------------------------------------------------------------------


def _date_bounds(path: Path, column: str | None) -> tuple[str | None, str | None]:
    if not column:
        return None, None
    metadata = pq.ParquetFile(path).metadata
    names = [metadata.schema.column(index).name for index in range(metadata.num_columns)]
    if column not in names:
        return None, None
    position = names.index(column)
    minimum = maximum = None
    for group in range(metadata.num_row_groups):
        stats = metadata.row_group(group).column(position).statistics
        if stats is None or not stats.has_min_max:
            continue
        minimum = stats.min if minimum is None else min(minimum, stats.min)
        maximum = stats.max if maximum is None else max(maximum, stats.max)
    return (str(minimum) if minimum is not None else None, str(maximum) if maximum is not None else None)


def canonical_inventory(root: Path) -> tuple[pd.DataFrame, str]:
    """List every canonical file with rows, bounds and content hash.

    The data version is the hash of the sorted (path, sha256) pairs, so any
    change to any canonical partition produces a new version.
    """
    canonical_root = root / "data" / "canonical"
    rows: list[dict[str, Any]] = []
    for path in sorted(canonical_root.rglob("*.parquet")):
        relative = path.relative_to(canonical_root)
        dataset = relative.parts[0]
        partition = "/".join(relative.parts[1:-1]) or None
        minimum, maximum = _date_bounds(path, DATE_COLUMNS.get(dataset))
        rows.append(
            {
                "dataset": dataset,
                "partition": partition,
                "path": relative.as_posix(),
                "rows": pq.ParquetFile(path).metadata.num_rows,
                "bytes": path.stat().st_size,
                "min_date": minimum,
                "max_date": maximum,
                "sha256": file_sha256(path),
            }
        )
    frame = pd.DataFrame(
        rows,
        columns=["dataset", "partition", "path", "rows", "bytes", "min_date", "max_date", "sha256"],
    )
    version = json_hash(list(zip(frame["path"], frame["sha256"])))
    return frame, version


def dataset_summary(inventory: pd.DataFrame) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for dataset, group in inventory.groupby("dataset"):
        summary[dataset] = {
            "files": int(len(group)),
            "rows": int(group["rows"].sum()),
            "bytes": int(group["bytes"].sum()),
            "min_date": group["min_date"].dropna().min() if group["min_date"].notna().any() else None,
            "max_date": group["max_date"].dropna().max() if group["max_date"].notna().any() else None,
        }
    return summary


# ---------------------------------------------------------------------------
# DuckDB catalog
# ---------------------------------------------------------------------------


def _parquet_exists(directory: Path) -> bool:
    return directory.exists() and any(directory.rglob("*.parquet"))


def _duckdb_glob(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _manifest_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    manifest_dir = root / "data" / "manifests"
    seen: set[str] = set()
    for path in sorted(manifest_dir.glob("*.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = manifest.get("quality_summary") or {}
        rows.append(
            {
                "run_id": manifest.get("run_id", path.stem),
                "started_at": manifest.get("started_at"),
                "finished_at": manifest.get("finished_at"),
                "status": manifest.get("status"),
                "mode": manifest.get("mode"),
                "provider": manifest.get("provider"),
                "provider_version": manifest.get("provider_version"),
                "code_version": (manifest.get("code_version") or {}).get("git_sha"),
                "config_hash": manifest.get("config_hash"),
                "data_version": manifest.get("data_version"),
                "expected_latest_date": manifest.get("expected_latest_date"),
                "blocking_issues": summary.get("blocking"),
                "warning_issues": summary.get("warning"),
                "manifest_path": path.relative_to(root).as_posix(),
            }
        )
        seen.add(rows[-1]["run_id"])
    # Interrupted runs leave checkpoints but no manifest; list them explicitly.
    for directory in sorted((root / "data" / "checkpoints").glob("run_id=*")):
        run_id = directory.name.split("=", 1)[1]
        if run_id not in seen:
            rows.append({"run_id": run_id, "status": "aborted_no_manifest"})
    return rows


ADJUSTED_VIEW_SQL = """
CREATE OR REPLACE VIEW daily_bars_adjusted AS
WITH latest AS (
    SELECT
        symbol,
        arg_max(hfq_factor, effective_date) AS hfq_latest,
        max({factor_as_of}) AS factor_as_of
    FROM adjustment_factors
    GROUP BY symbol
),
joined AS (
    SELECT b.*, f.hfq_factor
    FROM daily_bars AS b
    ASOF LEFT JOIN adjustment_factors AS f
      ON b.symbol = f.symbol
     AND b.trade_date >= f.effective_date
)
SELECT
    j.*,
    j.hfq_factor / l.hfq_latest AS qfq_factor,
    l.factor_as_of,
    CASE
        WHEN j.hfq_factor IS NULL THEN 'missing'
        WHEN l.factor_as_of IS NULL OR l.factor_as_of < j.trade_date THEN 'stale'
        ELSE 'ok'
    END AS factor_status,
    j.open * j.hfq_factor AS hfq_open,
    j.high * j.hfq_factor AS hfq_high,
    j.low * j.hfq_factor AS hfq_low,
    j.close * j.hfq_factor AS hfq_close,
    j.open * j.hfq_factor / l.hfq_latest AS qfq_open,
    j.high * j.hfq_factor / l.hfq_latest AS qfq_high,
    j.low * j.hfq_factor / l.hfq_latest AS qfq_low,
    j.close * j.hfq_factor / l.hfq_latest AS qfq_close
FROM joined AS j
LEFT JOIN latest AS l USING (symbol)
"""


def build_duckdb_catalog(root: Path) -> Path:
    """Materialize canonical parquet into a fresh DuckDB file and swap it in.

    Tables are copies (fast queries, no absolute paths baked into views); the
    parquet files stay the source of truth.  The build goes to a temporary
    file so readers never observe a half-built catalog; if the live file is
    locked by another process the swap fails with :class:`CatalogError`.
    """
    database_path = root / "data" / "market.duckdb"
    temp_path = root / "data" / "market.duckdb.building"
    for leftover in (temp_path, temp_path.with_suffix(".building.wal")):
        if leftover.exists():
            leftover.unlink()
    canonical_root = root / "data" / "canonical"
    try:
        with duckdb.connect(str(temp_path)) as con:
            for dataset in PARTITIONED_DATASETS + SINGLE_FILE_DATASETS:
                directory = canonical_root / dataset
                if not _parquet_exists(directory):
                    continue
                glob_path = _duckdb_glob(directory / "**" / "*.parquet")
                order = f" ORDER BY symbol, {DATE_COLUMNS[dataset]}" if dataset in (
                    "daily_bars", "adjustment_factors", "index_bars", "sw_index_bars") else ""
                con.execute(
                    f"CREATE TABLE {dataset} AS SELECT * FROM read_parquet("
                    f"'{glob_path}', union_by_name=true, hive_partitioning=false){order}"
                )
            tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
            if "security_master" in tables:
                con.execute(
                    "CREATE VIEW instruments AS SELECT * FROM security_master "
                    "WHERE status <> 'delisted'"
                )
            elif _parquet_exists(canonical_root / "instruments"):
                # Legacy store (before security_master): keep the old table.
                glob_path = _duckdb_glob(canonical_root / "instruments" / "**" / "*.parquet")
                con.execute(f"CREATE TABLE instruments AS SELECT * FROM read_parquet('{glob_path}')")
                tables.add("instruments")
            if {"daily_bars", "adjustment_factors"} <= tables:
                factor_columns = {row[0] for row in con.execute("DESCRIBE adjustment_factors").fetchall()}
                # Old-schema factor files carry no factor_as_of.
                con.execute(ADJUSTED_VIEW_SQL.format(
                    factor_as_of="factor_as_of" if "factor_as_of" in factor_columns else "CAST(NULL AS DATE)"
                ))
            if {"suspension_events", "daily_bars", "trading_calendar"} <= tables:
                con.execute(SUSPENSION_SPANS_SQL)
            runs = pd.DataFrame(
                _manifest_rows(root),
                columns=[
                    "run_id", "started_at", "finished_at", "status", "mode", "provider",
                    "provider_version", "code_version", "config_hash", "data_version",
                    "expected_latest_date", "blocking_issues", "warning_issues", "manifest_path",
                ],
            )
            con.register("runs_frame", runs)
            con.execute("CREATE TABLE ingestion_runs AS SELECT * FROM runs_frame ORDER BY run_id")
            con.unregister("runs_frame")
            catalog_rows = [
                (table, con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in sorted(tables)
            ]
            con.execute(
                "CREATE TABLE data_catalog (dataset VARCHAR, row_count BIGINT, "
                "refreshed_at TIMESTAMPTZ DEFAULT current_timestamp)"
            )
            con.executemany(
                "INSERT INTO data_catalog (dataset, row_count) VALUES (?, ?)", catalog_rows
            )
    except duckdb.Error as exc:
        raise CatalogError(f"Failed to build DuckDB catalog: {exc}") from exc
    try:
        # A leftover WAL of the old file would be replayed against the new one.
        stale_wal = database_path.with_name(database_path.name + ".wal")
        if stale_wal.exists():
            stale_wal.unlink()
        os.replace(temp_path, database_path)
    except OSError as exc:
        raise CatalogError(
            f"Built {temp_path} but could not replace {database_path}; "
            f"close other connections and run `quant-data catalog --rebuild`: {exc}"
        ) from exc
    return database_path


