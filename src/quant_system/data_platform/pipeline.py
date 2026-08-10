from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DataPlatformConfig, SymbolConfig
from .normalization import (
    normalize_calendar,
    normalize_daily_bars,
    normalize_adjustment_factors,
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

    def _daily_symbols(self, instruments: pd.DataFrame) -> tuple[SymbolConfig, ...]:
        if self.config.daily_universe == "configured":
            return self.config.symbols
        return tuple(
            SymbolConfig(
                symbol=str(row.symbol).zfill(6),
                name=str(row.name),
                group=str(row.exchange),
            )
            for row in instruments.itertuples(index=False)
        )

    def _daily_path(self, symbol: str) -> Path:
        return (
            self.root
            / "data"
            / "canonical"
            / "daily_bars"
            / f"symbol={symbol}"
            / "data.parquet"
        )

    def _factor_path(self, symbol: str) -> Path:
        return (
            self.root
            / "data"
            / "canonical"
            / "adjustment_factors"
            / f"symbol={symbol}"
            / "data.parquet"
        )

    def _existing_max_date(self, path: Path, column: str) -> date | None:
        if not path.exists():
            return None
        frame = pd.read_parquet(path, columns=[column])
        if frame.empty:
            return None
        values = pd.to_datetime(frame[column], errors="coerce").dropna()
        return values.max().date() if not values.empty else None

    @staticmethod
    def _merge_daily_bars(
        existing: pd.DataFrame, incoming: pd.DataFrame
    ) -> pd.DataFrame:
        if existing.empty:
            return incoming
        if incoming.empty:
            return existing
        frame = pd.concat([existing, incoming], ignore_index=True)
        frame = (
            frame.drop_duplicates(["symbol", "trade_date"], keep="last")
            .sort_values(["symbol", "trade_date"], ignore_index=True)
        )
        previous_close = frame.groupby("symbol")["close"].shift(1)
        frame["change_cny"] = frame["close"] - previous_close
        frame["pct_change"] = frame["change_cny"] / previous_close * 100
        frame["amplitude_pct"] = (
            (frame["high"] - frame["low"]) / previous_close * 100
        )
        return frame

    def _checkpoint_path(self, run_id: str, dataset: str) -> Path:
        return self.root / "data" / "checkpoints" / f"run_id={run_id}" / f"{dataset}.json"

    @staticmethod
    def _validate_adjustment_factors(
        frame: pd.DataFrame, symbol: str
    ) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        duplicate_count = int(
            frame.duplicated(["symbol", "effective_date"]).sum()
        )
        if duplicate_count:
            issues.append(
                QualityIssue(
                    "adjustment_factors",
                    "unique_symbol_date",
                    "blocking",
                    f"{symbol} 复权因子日期重复",
                    duplicate_count,
                )
            )
        for column in ["qfq_factor", "hfq_factor"]:
            invalid_count = int(
                (frame[column].notna() & (frame[column] <= 0)).sum()
            )
            if invalid_count:
                issues.append(
                    QualityIssue(
                        "adjustment_factors",
                        "positive_factor",
                        "blocking",
                        f"{symbol} 的 {column} 存在非正数",
                        invalid_count,
                    )
                )
            null_count = int(frame[column].isna().sum())
            if null_count:
                issues.append(
                    QualityIssue(
                        "adjustment_factors",
                        "factor_not_null",
                        "warning",
                        f"{symbol} 的 {column} 存在空值",
                        null_count,
                    )
                )
        return issues

    def run(self, mode: str = "incremental") -> dict[str, Any]:
        if mode not in {"full", "incremental"}:
            raise ValueError("mode must be one of: full, incremental")
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
        daily_symbols = self._daily_symbols(instruments)
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
        daily_checkpoint = {
            "run_id": run_id,
            "mode": mode,
            "universe": self.config.daily_universe,
            "requested_symbols": len(daily_symbols),
            "completed_symbols": [],
            "failed_symbols": [],
        }
        daily_checkpoint_path = self._checkpoint_path(run_id, "daily_bars")
        json_dump(daily_checkpoint_path, daily_checkpoint)
        files.append(str(daily_checkpoint_path))

        pending: dict[Any, tuple[SymbolConfig, str]] = {}
        for item in daily_symbols:
            existing_max = self._existing_max_date(self._daily_path(item.symbol), "trade_date")
            if (
                mode == "incremental"
                and existing_max is not None
                and expected_latest_date is not None
                and existing_max >= expected_latest_date
            ):
                dataset_stats[f"daily_bars:{item.symbol}"] = self._stats(
                    pd.read_parquet(self._daily_path(item.symbol)), "trade_date"
                )
                daily_checkpoint["completed_symbols"].append(
                    {"symbol": item.symbol, "status": "skipped", "last_date": str(existing_max)}
                )
                successful_symbols += 1
                continue
            if mode == "full" or existing_max is None:
                start_date = self.config.start_date
            else:
                start_date = max(
                    self.config.start_date,
                    (existing_max + timedelta(days=1)).strftime("%Y%m%d"),
                )
            pending[item.symbol] = (item, start_date)

        def fetch_daily(item: SymbolConfig, start_date: str) -> tuple[SymbolConfig, str, pd.DataFrame, pd.DataFrame]:
            raw = self.provider.fetch_daily_bars(
                item.symbol, start_date, self.config.end_date
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
            return item, start_date, raw, frame

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(fetch_daily, item, start_date): item
                for item, start_date in pending.values()
            }
            for completed_count, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                try:
                    item, start_date, raw, incoming = future.result()
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
                    existing_path = self._daily_path(item.symbol)
                    existing = (
                        pd.read_parquet(existing_path)
                        if mode == "incremental" and existing_path.exists()
                        else pd.DataFrame()
                    )
                    if incoming.empty and not existing.empty:
                        frame = existing
                        issues.append(
                            QualityIssue(
                                "daily_bars",
                                "incremental_no_new_rows",
                                "warning",
                                f"{item.symbol} 从 {start_date} 起没有新增行情，保留已有数据",
                            )
                        )
                    else:
                        frame = self._merge_daily_bars(existing, incoming)
                    if not frame.empty:
                        symbol_issues = validate_bars(frame, "daily_bars", item.symbol)
                        if (
                            expected_latest_date
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
                    daily_checkpoint["completed_symbols"].append(
                        {
                            "symbol": item.symbol,
                            "status": "updated",
                            "start_date": start_date,
                            "last_date": str(frame["trade_date"].max()) if not frame.empty else None,
                        }
                    )
                    successful_symbols += 1
                    if completed_count % 25 == 0 or completed_count == len(futures):
                        self.logger.info(
                            "Daily bars progress: %s/%s symbols",
                            completed_count,
                            len(futures),
                        )
                        json_dump(daily_checkpoint_path, daily_checkpoint)
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
                    daily_checkpoint["failed_symbols"].append(
                        {"symbol": item.symbol, "error": str(exc)}
                    )
                    json_dump(daily_checkpoint_path, daily_checkpoint)

        json_dump(daily_checkpoint_path, daily_checkpoint)

        factor_requested = 0
        factor_successful = 0
        if self.config.download_adjustment_factors:
            factor_checkpoint = {
                "run_id": run_id,
                "mode": mode,
                "requested_symbols": 0,
                "completed_symbols": [],
                "failed_symbols": [],
            }
            factor_checkpoint_path = self._checkpoint_path(
                run_id, "adjustment_factors"
            )
            json_dump(factor_checkpoint_path, factor_checkpoint)
            files.append(str(factor_checkpoint_path))
            factor_pending: dict[str, SymbolConfig] = {}
            for item in daily_symbols:
                factor_path = self._factor_path(item.symbol)
                if mode == "incremental" and factor_path.exists():
                    factor_frame = pd.read_parquet(factor_path)
                    dataset_stats[f"adjustment_factors:{item.symbol}"] = self._stats(
                        factor_frame, "effective_date"
                    )
                    factor_checkpoint["completed_symbols"].append(
                        {"symbol": item.symbol, "status": "skipped"}
                    )
                    factor_successful += 1
                else:
                    factor_pending[item.symbol] = item
            factor_requested = len(daily_symbols)
            factor_checkpoint["requested_symbols"] = factor_requested

            def fetch_factors(item: SymbolConfig) -> tuple[SymbolConfig, pd.DataFrame, pd.DataFrame]:
                raw = self.provider.fetch_adjustment_factors(
                    item.symbol, self.config.start_date, self.config.end_date
                )
                frame = normalize_adjustment_factors(
                    raw,
                    item.symbol,
                    run_id,
                    ingested_at,
                    source=self.provider.adjustment_sources.get(
                        item.symbol, "akshare.unknown"
                    ),
                )
                return item, raw, frame

            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                futures = {
                    executor.submit(fetch_factors, item): item
                    for item in factor_pending.values()
                }
                for completed_count, future in enumerate(as_completed(futures), start=1):
                    item = futures[future]
                    try:
                        item, raw, frame = future.result()
                        files.append(
                            str(
                                write_raw_frame(
                                    self.root,
                                    self.config.provider,
                                    "adjustment_factors",
                                    run_id,
                                    item.symbol,
                                    raw,
                                )
                            )
                        )
                        if frame.empty:
                            raise ValueError(f"{item.symbol} 没有返回复权因子")
                        issues.extend(
                            self._validate_adjustment_factors(frame, item.symbol)
                        )
                        files.append(
                            str(
                                write_canonical_frame(
                                    self.root,
                                    "adjustment_factors",
                                    frame,
                                    partition=f"symbol={item.symbol}",
                                )
                            )
                        )
                        dataset_stats[f"adjustment_factors:{item.symbol}"] = self._stats(
                            frame, "effective_date"
                        )
                        factor_checkpoint["completed_symbols"].append(
                            {"symbol": item.symbol, "status": "updated"}
                        )
                        factor_successful += 1
                        if completed_count % 25 == 0 or completed_count == len(futures):
                            self.logger.info(
                                "Adjustment factors progress: %s/%s symbols",
                                completed_count,
                                len(futures),
                            )
                            json_dump(factor_checkpoint_path, factor_checkpoint)
                    except Exception as exc:
                        self.logger.exception(
                            "Failed to ingest adjustment factors for %s", item.symbol
                        )
                        factor_checkpoint["failed_symbols"].append(
                            {"symbol": item.symbol, "error": str(exc)}
                        )
                        issues.append(
                            QualityIssue(
                                "adjustment_factors",
                                "ingestion_success",
                                "warning",
                                f"{item.symbol} 复权因子下载或标准化失败: {exc}",
                            )
                        )
                        json_dump(factor_checkpoint_path, factor_checkpoint)
            json_dump(factor_checkpoint_path, factor_checkpoint)

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
            "mode": mode,
            "config_hash": json_hash(asdict(self.config)),
            "config": asdict(self.config),
            "successful_symbols": successful_symbols,
            "requested_symbols": len(daily_symbols),
            "successful_factor_symbols": factor_successful,
            "requested_factor_symbols": factor_requested,
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
            len(daily_symbols),
        )
        return manifest
