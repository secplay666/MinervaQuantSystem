from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DataPlatformConfig
from .normalization import (
    normalize_calendar,
    normalize_daily_bars,
    normalize_index_bars,
    normalize_instruments,
    normalize_market_snapshot,
)
from .providers import AkShareProvider
from .quality import (
    QualityIssue,
    quality_summary,
    validate_bars,
    validate_calendar,
    validate_instruments,
)
from .reporting import write_quality_report
from .storage import (
    build_duckdb_catalog,
    write_canonical_frame,
    write_raw_frame,
)
from .utils import ensure_directories, json_dump, json_hash, make_run_id, utc_now_iso


class IngestionPipeline:
    def __init__(self, root: Path, config: DataPlatformConfig) -> None:
        self.root = root.resolve()
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        if config.provider != "akshare":
            raise ValueError(f"Unsupported provider: {config.provider}")
        self.provider = AkShareProvider(
            expected_version=config.provider_version,
            max_retries=config.max_retries,
            request_pause_seconds=config.request_pause_seconds,
        )

    @staticmethod
    def _stats(frame: pd.DataFrame, date_column: str | None = None) -> dict[str, object]:
        stats: dict[str, object] = {"rows": len(frame)}
        if date_column and not frame.empty and date_column in frame.columns:
            stats["min_date"] = str(frame[date_column].min())
            stats["max_date"] = str(frame[date_column].max())
        return stats

    def run(self) -> dict[str, Any]:
        ensure_directories(self.root)
        run_id = make_run_id()
        started_at = utc_now_iso()
        ingested_at = started_at
        issues: list[QualityIssue] = []
        dataset_stats: dict[str, dict[str, object]] = {}
        files: list[str] = []
        errors: list[dict[str, str]] = []

        self.logger.info("Starting AKShare ingestion run %s", run_id)

        instruments_raw = self.provider.fetch_instruments()
        files.append(
            str(
                write_raw_frame(
                    self.root,
                    self.config.provider,
                    "instruments",
                    run_id,
                    "all_a_shares",
                    instruments_raw,
                )
            )
        )
        instruments = normalize_instruments(instruments_raw, run_id, ingested_at)
        issues.extend(validate_instruments(instruments))
        configured_symbols = {item.symbol for item in self.config.symbols}
        available_symbols = set(instruments["symbol"])
        missing_symbols = sorted(configured_symbols - available_symbols)
        if missing_symbols:
            issues.append(
                QualityIssue(
                    "instruments",
                    "configured_symbols_exist",
                    "blocking",
                    f"配置中的证券不存在于当前证券列表: {', '.join(missing_symbols)}",
                    len(missing_symbols),
                )
            )
        files.append(
            str(write_canonical_frame(self.root, "instruments", instruments))
        )
        dataset_stats["instruments"] = self._stats(instruments)

        calendar_raw = self.provider.fetch_trading_calendar()
        files.append(
            str(
                write_raw_frame(
                    self.root,
                    self.config.provider,
                    "trading_calendar",
                    run_id,
                    "cn_open_dates",
                    calendar_raw,
                )
            )
        )
        calendar = normalize_calendar(
            calendar_raw,
            self.config.start_date,
            self.config.end_date,
            run_id,
            ingested_at,
        )
        issues.extend(validate_calendar(calendar))
        files.append(
            str(write_canonical_frame(self.root, "trading_calendar", calendar))
        )
        dataset_stats["trading_calendar"] = self._stats(calendar, "trade_date")
        expected_latest_date = calendar["trade_date"].max() if not calendar.empty else None

        successful_symbols = 0
        for item in self.config.symbols:
            self.logger.info("Downloading daily bars for %s %s", item.symbol, item.name)
            try:
                raw = self.provider.fetch_daily_bars(
                    item.symbol, self.config.start_date, self.config.end_date
                )
                files.append(
                    str(
                        write_raw_frame(
                            self.root,
                            self.config.provider,
                            "daily_bars",
                            run_id,
                            item.symbol,
                            raw,
                        )
                    )
                )
                frame = normalize_daily_bars(
                    raw,
                    item.symbol,
                    run_id,
                    ingested_at,
                    source=self.provider.daily_sources.get(
                        item.symbol, "akshare.unknown"
                    ),
                )
                symbol_issues = validate_bars(frame, "daily_bars", item.symbol)
                if (
                    expected_latest_date
                    and not frame.empty
                    and frame["trade_date"].max() < expected_latest_date
                ):
                    symbol_issues.append(
                        QualityIssue(
                            "daily_bars",
                            "latest_trade_date",
                            "warning",
                            f"{item.symbol} 最新行情 {frame['trade_date'].max()} "
                            f"早于交易日历 {expected_latest_date}",
                        )
                    )
                issues.extend(symbol_issues)
                files.append(
                    str(
                        write_canonical_frame(
                            self.root,
                            "daily_bars",
                            frame,
                            partition=f"symbol={item.symbol}",
                        )
                    )
                )
                dataset_stats[f"daily_bars:{item.symbol}"] = self._stats(
                    frame, "trade_date"
                )
                successful_symbols += 1
            except Exception as exc:
                self.logger.exception("Failed to ingest %s", item.symbol)
                errors.append(
                    {"dataset": "daily_bars", "subject": item.symbol, "error": str(exc)}
                )
                issues.append(
                    QualityIssue(
                        "daily_bars",
                        "ingestion_success",
                        "blocking",
                        f"{item.symbol} 下载或标准化失败: {exc}",
                    )
                )

        for item in self.config.indices:
            self.logger.info("Downloading index bars for %s %s", item.symbol, item.name)
            try:
                raw = self.provider.fetch_index_daily(item.symbol)
                files.append(
                    str(
                        write_raw_frame(
                            self.root,
                            self.config.provider,
                            "index_bars",
                            run_id,
                            item.symbol,
                            raw,
                        )
                    )
                )
                frame = normalize_index_bars(
                    raw,
                    item.symbol,
                    item.name,
                    self.config.index_start_date,
                    self.config.end_date,
                    run_id,
                    ingested_at,
                    source=self.provider.index_sources.get(
                        item.symbol, "akshare.unknown"
                    ),
                )
                issues.extend(validate_bars(frame, "index_bars", item.symbol))
                files.append(
                    str(
                        write_canonical_frame(
                            self.root,
                            "index_bars",
                            frame,
                            partition=f"symbol={item.symbol}",
                        )
                    )
                )
                dataset_stats[f"index_bars:{item.symbol}"] = self._stats(
                    frame, "trade_date"
                )
            except Exception as exc:
                self.logger.exception("Failed to ingest index %s", item.symbol)
                errors.append(
                    {"dataset": "index_bars", "subject": item.symbol, "error": str(exc)}
                )
                issues.append(
                    QualityIssue(
                        "index_bars",
                        "ingestion_success",
                        "warning",
                        f"{item.symbol} 指数下载失败: {exc}",
                    )
                )

        try:
            snapshot_raw = self.provider.fetch_market_snapshot()
            files.append(
                str(
                    write_raw_frame(
                        self.root,
                        self.config.provider,
                        "market_snapshot",
                        run_id,
                        "all_a_shares",
                        snapshot_raw,
                    )
                )
            )
            snapshot_date = str(expected_latest_date or self.config.end_date)
            snapshot = normalize_market_snapshot(
                snapshot_raw,
                snapshot_date,
                run_id,
                ingested_at,
                source=self.provider.snapshot_source,
            )
            files.append(
                str(
                    write_canonical_frame(
                        self.root,
                        "market_snapshot",
                        snapshot,
                        partition=f"snapshot_date={snapshot_date}",
                    )
                )
            )
            dataset_stats["market_snapshot"] = self._stats(snapshot, "snapshot_date")
        except Exception as exc:
            self.logger.exception("Failed to ingest market snapshot")
            errors.append(
                {"dataset": "market_snapshot", "subject": "all", "error": str(exc)}
            )
            issues.append(
                QualityIssue(
                    "market_snapshot",
                    "ingestion_success",
                    "warning",
                    f"全市场快照下载失败: {exc}",
                )
            )

        summary = quality_summary(issues)
        if successful_symbols == 0:
            status = "failed"
        elif summary["blocking"]:
            status = "partial"
        else:
            status = "complete"
        finished_at = utc_now_iso()
        report_json, report_markdown = write_quality_report(
            self.root, run_id, issues, dataset_stats
        )
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "provider": self.config.provider,
            "provider_version": self.provider.version,
            "config_hash": json_hash(asdict(self.config)),
            "config": asdict(self.config),
            "successful_symbols": successful_symbols,
            "requested_symbols": len(self.config.symbols),
            "quality_summary": summary,
            "dataset_stats": dataset_stats,
            "errors": errors,
            "files": files,
            "quality_report_json": str(report_json),
            "quality_report_markdown": str(report_markdown),
        }
        manifest_path = self.root / "data" / "manifests" / f"{run_id}.json"
        json_dump(manifest_path, manifest)
        database_path = build_duckdb_catalog(self.root, manifest)
        manifest["manifest_path"] = str(manifest_path)
        manifest["database_path"] = str(database_path)
        json_dump(manifest_path, manifest)
        self.logger.info(
            "Ingestion run %s finished with status=%s, symbols=%s/%s",
            run_id,
            status,
            successful_symbols,
            len(self.config.symbols),
        )
        return manifest
