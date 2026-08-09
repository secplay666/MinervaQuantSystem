from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


def write_parquet_atomic(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temp_path, index=False, engine="pyarrow", compression="zstd")
    temp_path.replace(path)
    return path


def write_raw_frame(
    root: Path, provider: str, dataset: str, run_id: str, name: str, frame: pd.DataFrame
) -> Path:
    return write_parquet_atomic(
        frame,
        root
        / "data"
        / "raw"
        / provider
        / dataset
        / f"run_id={run_id}"
        / f"{name}.parquet",
    )


def write_canonical_frame(
    root: Path, dataset: str, frame: pd.DataFrame, partition: str | None = None
) -> Path:
    directory = root / "data" / "canonical" / dataset
    if partition:
        directory /= partition
    return write_parquet_atomic(frame, directory / "data.parquet")


def _duckdb_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _parquet_files(path: Path) -> list[Path]:
    return sorted(path.rglob("*.parquet")) if path.exists() else []


def build_duckdb_catalog(root: Path, manifest: dict[str, Any]) -> Path:
    database_path = root / "data" / "market.duckdb"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_paths = {
        "instruments": root / "data" / "canonical" / "instruments",
        "trading_calendar": root / "data" / "canonical" / "trading_calendar",
        "daily_bars": root / "data" / "canonical" / "daily_bars",
        "adjustment_factors": root / "data" / "canonical" / "adjustment_factors",
        "index_bars": root / "data" / "canonical" / "index_bars",
        "market_snapshot": root / "data" / "canonical" / "market_snapshot",
    }
    with duckdb.connect(str(database_path)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                run_id VARCHAR PRIMARY KEY,
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                provider VARCHAR,
                provider_version VARCHAR,
                status VARCHAR,
                manifest_json JSON
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS data_catalog (
                dataset VARCHAR PRIMARY KEY,
                file_count BIGINT,
                row_count BIGINT,
                refreshed_at TIMESTAMPTZ,
                parquet_root VARCHAR
            )
            """
        )
        for dataset, directory in dataset_paths.items():
            files = _parquet_files(directory)
            if not files:
                continue
            glob_path = _duckdb_path(directory / "**" / "*.parquet")
            con.execute(
                f"""
                CREATE OR REPLACE VIEW {dataset} AS
                SELECT * FROM read_parquet(
                    '{glob_path}', union_by_name=true, hive_partitioning=false
                )
                """
            )
            row_count = con.execute(f"SELECT COUNT(*) FROM {dataset}").fetchone()[0]
            con.execute("DELETE FROM data_catalog WHERE dataset = ?", [dataset])
            con.execute(
                """
                INSERT INTO data_catalog
                VALUES (?, ?, ?, current_timestamp, ?)
                """,
                [dataset, len(files), row_count, str(directory.resolve())],
            )
        if dataset_paths["daily_bars"].exists() and dataset_paths[
            "adjustment_factors"
        ].exists():
            con.execute(
                """
                CREATE OR REPLACE VIEW daily_bars_adjusted AS
                WITH bars_with_factors AS (
                    SELECT
                        b.*,
                        COALESCE(f.qfq_factor, 1.0) AS qfq_factor,
                        COALESCE(f.hfq_factor, 1.0) AS hfq_factor
                    FROM daily_bars AS b
                    ASOF LEFT JOIN adjustment_factors AS f
                      ON b.symbol = f.symbol
                     AND b.trade_date >= f.effective_date
                )
                SELECT
                    *,
                    open * qfq_factor AS qfq_open,
                    high * qfq_factor AS qfq_high,
                    low * qfq_factor AS qfq_low,
                    close * qfq_factor AS qfq_close,
                    open * hfq_factor AS hfq_open,
                    high * hfq_factor AS hfq_high,
                    low * hfq_factor AS hfq_low,
                    close * hfq_factor AS hfq_close
                FROM bars_with_factors
                """
            )
        con.execute("DELETE FROM ingestion_runs WHERE run_id = ?", [manifest["run_id"]])
        con.execute(
            """
            INSERT INTO ingestion_runs
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                manifest["run_id"],
                manifest["started_at"],
                manifest["finished_at"],
                manifest["provider"],
                manifest["provider_version"],
                manifest["status"],
                json.dumps(manifest, ensure_ascii=False, default=str),
            ],
        )
    return database_path
