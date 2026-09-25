from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import run_audit
from .config import DataPlatformConfig
from .normalization import (
    as_date,
    build_risk_warning_intervals,
    calendar_open_dates,
    clip_bars,
    drop_invalid_price_rows,
    merge_daily_bars,
    merge_index_bars,
    merge_suspension_events,
    normalize_adjustment_factors,
    normalize_baidu_suspensions,
    normalize_calendar,
    normalize_daily_bars,
    normalize_index_bars,
    normalize_market_snapshot,
    normalize_security_master,
    normalize_sz_name_changes,
    normalize_tfp_suspensions,
)
from .providers import AkShareProvider
from .providers.base import FetchResult, MarketDataProvider
from .quality import (
    QualityIssue,
    has_blocking,
    quality_summary,
    validate_adjustment_factors,
    validate_bars,
    validate_calendar,
    validate_index_refresh,
    validate_security_master,
    validate_volume_units,
)
from .reporting import write_quality_report
from .sessions import latest_final_session, parse_hhmm, session_offset, shanghai_now, snapshot_session
from .storage import (
    CatalogError,
    build_duckdb_catalog,
    canonical_inventory,
    canonical_path,
    dataset_summary,
    read_canonical,
    write_canonical_frame,
    write_parquet_atomic,
    write_raw_frame,
)
from .utils import code_version, ensure_directories, json_dump, make_run_id, run_id_to_iso, utc_now_iso

MAX_RECORDED_ERRORS = 200
BAIDU_MAX_CONSECUTIVE_FAILURES = 10


@dataclass(frozen=True)
class UniverseItem:
    symbol: str
    name: str
    list_date: date | None
    delist_date: date | None
    status: str

    @property
    def is_delisted(self) -> bool:
        return self.status == "delisted"


@dataclass
class RunContext:
    run_id: str
    mode: str
    started_at: str
    ingested_at: str
    start_date: date
    issues: list[QualityIssue] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    raw_files: list[str] = field(default_factory=list)
    open_dates: list[date] = field(default_factory=list)
    expected_latest: date | None = None
    master: pd.DataFrame | None = None
    first_bar_dates: dict[str, date] = field(default_factory=dict)
    summaries: dict[str, Any] = field(default_factory=dict)

    def count(self, step: str, key: str, amount: int = 1) -> None:
        bucket = self.counters.setdefault(step, {})
        bucket[key] = bucket.get(key, 0) + amount

    def error(self, dataset: str, subject: str, exc: BaseException) -> None:
        self.count("errors", dataset)
        if len(self.errors) < MAX_RECORDED_ERRORS:
            self.errors.append({"dataset": dataset, "subject": subject, "error": str(exc)})


class IngestionPipeline:
    def __init__(
        self,
        root: Path,
        config: DataPlatformConfig,
        provider: MarketDataProvider | None = None,
        clock: Callable[[], datetime] = shanghai_now,
    ) -> None:
        self.root = root.resolve()
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        if config.provider != "akshare":
            raise ValueError(f"Unsupported provider: {config.provider}")
        self.provider = provider or AkShareProvider(
            expected_version=config.provider_version,
            max_retries=config.max_retries,
            request_pause_seconds=config.request_pause_seconds,
            timeout_seconds=config.http_timeout_seconds,
        )
        self.clock = clock
        self.final_time = parse_hhmm(config.session_final_time)

    # ------------------------------------------------------------------ run

    def run(self, mode: str = "incremental") -> dict[str, Any]:
        if mode not in {"full", "incremental"}:
            raise ValueError("mode must be one of: full, incremental")
        ensure_directories(self.root)
        run_id = make_run_id()
        ctx = RunContext(
            run_id=run_id,
            mode=mode,
            started_at=utc_now_iso(),
            ingested_at=run_id_to_iso(run_id),
            start_date=pd.to_datetime(self.config.start_date).date(),
        )
        manifest_path = self.root / "data" / "manifests" / f"{run_id}.json"
        json_dump(manifest_path, {"run_id": run_id, "status": "running", "mode": mode,
                                  "started_at": ctx.started_at})
        self.logger.info("Starting ingestion run %s (mode=%s)", run_id, mode)
        fatal: BaseException | None = None
        try:
            self._run_steps(ctx)
        except Exception as exc:  # recorded in the manifest; never silently lost
            self.logger.exception("Ingestion run %s aborted", run_id)
            fatal = exc
            ctx.issues.append(QualityIssue("run", "completed", "blocking", f"运行中止: {exc}"))
        return self._finalize(ctx, manifest_path, fatal)

    def _run_steps(self, ctx: RunContext) -> None:
        self._ingest_calendar(ctx)
        self._ingest_security_master(ctx)
        universe = self._daily_universe(ctx)
        self._ingest_daily_bars(ctx, universe)
        if self.config.download_adjustment_factors:
            self._ingest_adjustment_factors(ctx, universe)
        self._ingest_indices(ctx)
        if self.config.download_status_history:
            self._ingest_status_history(ctx)
        self._ingest_market_snapshot(ctx)
        self._audit(ctx)

    # ------------------------------------------------------------ helpers

    def _raw(self, ctx: RunContext, dataset: str, name: str, frame: pd.DataFrame,
             source: str | None = None) -> None:
        path = write_raw_frame(self.root, self.config.provider, dataset, ctx.run_id, name, frame, source)
        ctx.raw_files.append(path.relative_to(self.root).as_posix())

    def _quarantine(self, ctx: RunContext, dataset: str, partition: str, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        path = self.root / "data" / "quarantine" / f"run_id={ctx.run_id}" / dataset / partition / "data.parquet"
        write_parquet_atomic(frame, path)

    def _checkpoint(self, ctx: RunContext, dataset: str, payload: dict[str, Any]) -> None:
        json_dump(self.root / "data" / "checkpoints" / f"run_id={ctx.run_id}" / f"{dataset}.json", payload)

    @staticmethod
    def _date_str(value: date) -> str:
        return value.strftime("%Y%m%d")

    # ----------------------------------------------------------- calendar

    def _ingest_calendar(self, ctx: RunContext) -> None:
        raw = self.provider.fetch_trading_calendar()
        self._raw(ctx, "trading_calendar", "cn_open_dates", raw, "akshare.tool_trade_date_hist_sina")
        ctx.open_dates = calendar_open_dates(raw)
        now = self.clock()
        explicit_end = pd.to_datetime(self.config.end_date).date() if self.config.end_date else None
        ctx.expected_latest = latest_final_session(ctx.open_dates, now, self.final_time, explicit_end)
        if ctx.expected_latest is None:
            raise RuntimeError("交易日历中没有已收盘的交易日")
        calendar = normalize_calendar(raw, ctx.start_date, ctx.expected_latest, ctx.run_id, ctx.ingested_at)
        issues = validate_calendar(calendar, now.date(), ctx.open_dates[-1] if ctx.open_dates else None)
        ctx.issues.extend(issues)
        if has_blocking(issues):
            raise RuntimeError("交易日历未通过质量检查")
        write_canonical_frame(self.root, "trading_calendar", calendar)
        self.logger.info("Latest final session: %s (clock %s)", ctx.expected_latest, now.isoformat())

    # ------------------------------------------------------ security master

    def _previous_master(self) -> pd.DataFrame | None:
        previous = read_canonical(self.root, "security_master")
        if previous is not None:
            return previous
        legacy = read_canonical(self.root, "instruments")
        if legacy is None:
            return None
        legacy = legacy[["symbol", "name"]].copy()
        legacy["status"] = "listed"
        return legacy

    def _ingest_security_master(self, ctx: RunContext) -> None:
        lists = self.provider.fetch_security_lists()
        for name, frame in lists.items():
            self._raw(ctx, "security_lists", name, frame, f"akshare.security_lists.{name}")
        manual = [asdict(item) for item in self.config.manual_delistings]
        master = normalize_security_master(lists, manual, ctx.run_id, ctx.ingested_at)
        previous = self._previous_master()
        issues = validate_security_master(master, previous, self.config.max_listing_shrink_ratio)
        ctx.issues.extend(issues)
        if has_blocking(issues):
            if previous is None or "list_date" not in previous.columns:
                raise RuntimeError("证券主数据未通过质量检查且没有可用的上一版本")
            self.logger.error("Security master rejected; keeping the previous version")
            ctx.master = previous
            return
        if previous is not None:
            master = self._carry_forward_removed(ctx, master, previous)
        write_canonical_frame(self.root, "security_master", master)
        legacy = self.root / "data" / "canonical" / "instruments"
        if legacy.exists():
            # Superseded by security_master; kept (not deleted) for audit.
            target = self.root / "data" / "archive" / f"instruments_{ctx.run_id}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(target))
        ctx.master = master
        counts = master["status"].value_counts().to_dict()
        ctx.summaries["security_master"] = {key: int(value) for key, value in counts.items()}

    def _carry_forward_removed(
        self, ctx: RunContext, master: pd.DataFrame, previous: pd.DataFrame
    ) -> pd.DataFrame:
        """Symbols that vanish from every list without a delisting record.

        BSE delistings are not published by any endpoint, so a disappearing
        code is kept as delisted (unknown date) and flagged for review.
        """
        live_before = previous[previous["status"] != "delisted"]
        removed = live_before[~live_before["symbol"].isin(master["symbol"])]
        if removed.empty:
            return master
        rows = []
        for row in removed.itertuples(index=False):
            record = {column: getattr(row, column, None) for column in master.columns}
            record.update(
                status="delisted",
                delist_date=None,
                source="inferred.missing_from_listing",
                run_id=ctx.run_id,
                ingested_at=ctx.ingested_at,
            )
            rows.append(record)
        ctx.issues.append(
            QualityIssue(
                "security_master",
                "removed_without_delisting",
                "warning",
                "以下证券从交易所列表消失但无退市记录，已标记为退市（日期未知），请在 manual_delistings 中补充: "
                + ", ".join(removed["symbol"].head(50)),
                int(len(removed)),
            )
        )
        return pd.concat([master, pd.DataFrame(rows, columns=master.columns)], ignore_index=True)

    def _daily_universe(self, ctx: RunContext) -> list[UniverseItem]:
        master = ctx.master
        assert master is not None and ctx.expected_latest is not None
        if self.config.daily_universe == "configured":
            configured = {item.symbol for item in self.config.symbols}
            missing = sorted(configured - set(master["symbol"]))
            if missing:
                ctx.issues.append(
                    QualityIssue(
                        "security_master",
                        "configured_symbols_exist",
                        "blocking",
                        f"配置中的证券不存在于证券主数据: {', '.join(missing)}",
                        len(missing),
                    )
                )
            master = master[master["symbol"].isin(configured)]
        items: list[UniverseItem] = []
        for row in master.itertuples(index=False):
            list_date = as_date(row.list_date)
            delist_date = as_date(row.delist_date)
            if list_date is not None and list_date > ctx.expected_latest:
                continue
            if delist_date is not None and delist_date < ctx.start_date:
                continue
            items.append(UniverseItem(row.symbol, row.name, list_date, delist_date, row.status))
        return items

    # --------------------------------------------------------- daily bars

    def _bar_window(self, ctx: RunContext, item: UniverseItem) -> tuple[date, date]:
        start = max(ctx.start_date, item.list_date) if item.list_date else ctx.start_date
        end = ctx.expected_latest
        if item.delist_date is not None:
            end = min(end, item.delist_date)
        return start, end

    def _fetch_start(
        self, ctx: RunContext, window_start: date, bounds: tuple[date, date] | None
    ) -> date:
        if ctx.mode == "full" or bounds is None:
            return window_start
        existing_min, existing_max = bounds
        first_session = next((d for d in ctx.open_dates if d >= window_start), window_start)
        if existing_min > first_session:
            return window_start  # head missing (e.g. start_date moved earlier)
        # Re-fetch a few sessions so late vendor revisions replace old rows.
        overlap_start = session_offset(ctx.open_dates, existing_max, self.config.overlap_sessions)
        return max(window_start, overlap_start)

    def _partition_bounds(self, dataset: str, symbol: str) -> tuple[date, date] | None:
        path = canonical_path(self.root, dataset, f"symbol={symbol}")
        if not path.exists():
            return None
        dates = pd.read_parquet(path, columns=["trade_date"])["trade_date"]
        return (dates.min(), dates.max()) if not dates.empty else None

    def _ingest_daily_bars(self, ctx: RunContext, universe: list[UniverseItem]) -> None:
        step = "daily_bars"
        checkpoint: dict[str, Any] = {"run_id": ctx.run_id, "mode": ctx.mode, "requested": len(universe),
                                      "completed": [], "failed": []}
        pending: dict[str, tuple[UniverseItem, date, date]] = {}
        for item in universe:
            window_start, window_end = self._bar_window(ctx, item)
            bounds = self._partition_bounds(step, item.symbol)
            if bounds is not None:
                ctx.first_bar_dates[item.symbol] = bounds[0]
            if window_end < window_start:
                ctx.count(step, "empty_window")
                continue
            if ctx.mode == "incremental" and item.is_delisted and bounds is not None:
                ctx.count(step, "frozen_delisted")
                continue
            pending[item.symbol] = (item, self._fetch_start(ctx, window_start, bounds), window_end)
        ctx.count(step, "requested", len(pending))

        def fetch(symbol: str) -> FetchResult:
            _, fetch_start, window_end = pending[symbol]
            return self.provider.fetch_daily_bars(symbol, self._date_str(fetch_start), self._date_str(window_end))

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {executor.submit(fetch, symbol): symbol for symbol in pending}
            for done, future in enumerate(as_completed(futures), start=1):
                symbol = futures[future]
                item, fetch_start, window_end = pending[symbol]
                try:
                    result = future.result()
                    existing = (
                        read_canonical(self.root, step, f"symbol={symbol}")
                        if ctx.mode == "incremental" else None
                    )
                    status = self._store_daily_bars(ctx, item, fetch_start, window_end, existing, result)
                    checkpoint["completed"].append({"symbol": symbol, "status": status})
                except Exception as exc:
                    self.logger.warning("Daily bars failed for %s: %s", symbol, exc)
                    ctx.error(step, symbol, exc)
                    ctx.count(step, "failed")
                    checkpoint["failed"].append({"symbol": symbol, "error": str(exc)})
                if done % 100 == 0 or done == len(futures):
                    self.logger.info("Daily bars progress: %s/%s", done, len(futures))
                    self._checkpoint(ctx, step, checkpoint)
        self._checkpoint(ctx, step, checkpoint)
        failed = ctx.counters.get(step, {}).get("failed", 0)
        if pending and failed / len(pending) > 1 - self.config.min_latest_coverage:
            ctx.issues.append(
                QualityIssue(step, "ingestion_success_ratio", "blocking",
                             f"{failed}/{len(pending)} 只证券日线下载失败", failed)
            )
        elif failed:
            ctx.issues.append(
                QualityIssue(step, "ingestion_success_ratio", "warning",
                             f"{failed}/{len(pending)} 只证券日线下载失败，下次增量运行会重试", failed)
            )

    def _store_daily_bars(
        self,
        ctx: RunContext,
        item: UniverseItem,
        fetch_start: date,
        window_end: date,
        existing: pd.DataFrame | None,
        result: FetchResult,
    ) -> str:
        step = "daily_bars"
        symbol = item.symbol
        self._raw(ctx, step, symbol, result.frame, result.source)
        incoming = normalize_daily_bars(result.frame, symbol, ctx.run_id, ctx.ingested_at, result.source)
        incoming, dropped = drop_invalid_price_rows(incoming)
        if dropped:
            ctx.count(step, "invalid_price_rows_dropped", dropped)
        window_start, _ = self._bar_window(ctx, item)
        merged = merge_daily_bars(existing, incoming)
        merged, clipped = clip_bars(merged, window_start, window_end)
        if clipped:
            ctx.count(step, "rows_outside_listing_dropped", clipped)
        if merged.empty:
            severity = "warning" if item.is_delisted else "blocking"
            ctx.issues.append(
                QualityIssue(step, "no_data", severity,
                             f"{symbol} 在 {fetch_start}~{window_end} 没有任何日线", symbol=symbol)
            )
            ctx.count(step, "no_data")
            return "no_data"
        issues = validate_bars(merged, step, symbol) + validate_volume_units(merged, symbol)
        ctx.issues.extend(issues)
        if has_blocking(issues):
            self._quarantine(ctx, step, f"symbol={symbol}", merged)
            ctx.count(step, "quarantined")
            return "quarantined"
        write_canonical_frame(self.root, step, merged, partition=f"symbol={symbol}")
        ctx.first_bar_dates[symbol] = merged["trade_date"].min()
        ctx.count(step, "updated")
        if incoming.empty and existing is not None and not existing.empty:
            ctx.count(step, "no_new_rows")
        return "updated"

    # ------------------------------------------------- adjustment factors

    def _ingest_adjustment_factors(self, ctx: RunContext, universe: list[UniverseItem]) -> None:
        step = "adjustment_factors"
        pending: list[UniverseItem] = []
        for item in universe:
            path = canonical_path(self.root, step, f"symbol={item.symbol}")
            # Factors are re-anchored snapshots, not an append-only log, so
            # every live symbol is refreshed each run; delisted ones are final.
            if ctx.mode == "incremental" and item.is_delisted and path.exists():
                ctx.count(step, "frozen_delisted")
                continue
            if item.symbol not in ctx.first_bar_dates:
                ctx.count(step, "no_bars")
                continue
            pending.append(item)
        ctx.count(step, "requested", len(pending))
        checkpoint: dict[str, Any] = {"run_id": ctx.run_id, "requested": len(pending),
                                      "completed": [], "failed": []}
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(self.provider.fetch_adjustment_factors, item.symbol): item
                for item in pending
            }
            for done, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                try:
                    status = self._store_factors(ctx, item, future.result())
                    checkpoint["completed"].append({"symbol": item.symbol, "status": status})
                except Exception as exc:
                    self.logger.warning("Adjustment factors failed for %s: %s", item.symbol, exc)
                    ctx.error(step, item.symbol, exc)
                    ctx.count(step, "failed")
                    checkpoint["failed"].append({"symbol": item.symbol, "error": str(exc)})
                if done % 200 == 0 or done == len(futures):
                    self.logger.info("Adjustment factors progress: %s/%s", done, len(futures))
                    self._checkpoint(ctx, step, checkpoint)
        self._checkpoint(ctx, step, checkpoint)
        refreshed = ctx.counters.get(step, {}).get("refreshed", 0)
        if pending and refreshed / len(pending) < self.config.min_factor_success_ratio:
            ctx.issues.append(
                QualityIssue(step, "refresh_success_ratio", "blocking",
                             f"仅 {refreshed}/{len(pending)} 只证券复权因子刷新成功", len(pending) - refreshed)
            )

    def _store_factors(self, ctx: RunContext, item: UniverseItem, result: FetchResult) -> str:
        step = "adjustment_factors"
        self._raw(ctx, step, item.symbol, result.frame, result.source)
        frame = normalize_adjustment_factors(
            result.frame, item.symbol, ctx.run_id, ctx.ingested_at, result.source,
            factor_as_of=ctx.expected_latest,
        )
        previous = read_canonical(self.root, step, f"symbol={item.symbol}")
        issues = validate_adjustment_factors(frame, item.symbol, previous, ctx.first_bar_dates.get(item.symbol))
        ctx.issues.extend(issues)
        if has_blocking(issues):
            self._quarantine(ctx, step, f"symbol={item.symbol}", frame)
            ctx.count(step, "quarantined")
            return "quarantined"
        if previous is not None and not previous.empty:
            ctx.count(step, "new_events", int((~frame["effective_date"].isin(previous["effective_date"])).sum()))
        write_canonical_frame(self.root, step, frame, partition=f"symbol={item.symbol}")
        ctx.count(step, "refreshed")
        if "derived" in result.source:
            ctx.count(step, "derived_fallback")
        return "refreshed"

    # -------------------------------------------------------------- index

    def _ingest_indices(self, ctx: RunContext) -> None:
        step = "index_bars"
        index_start = pd.to_datetime(self.config.index_start_date).date()
        for item in self.config.indices:
            try:
                existing = read_canonical(self.root, step, f"symbol={item.symbol}")
                if ctx.mode == "incremental" and existing is not None and not existing.empty:
                    fetch_start = session_offset(ctx.open_dates, existing["trade_date"].max(),
                                                 self.config.overlap_sessions)
                else:
                    existing, fetch_start = None, index_start
                result = self.provider.fetch_index_daily(
                    item.symbol, self._date_str(fetch_start), self._date_str(ctx.expected_latest)
                )
                self._raw(ctx, step, item.symbol, result.frame, result.source)
                incoming = normalize_index_bars(
                    result.frame, item.symbol, item.name, index_start, ctx.expected_latest,
                    ctx.run_id, ctx.ingested_at, source=result.source,
                )
                issues = validate_index_refresh(incoming, existing, item.symbol)
                merged = merge_index_bars(existing, incoming)
                previous = read_canonical(self.root, step, f"symbol={item.symbol}")
                if previous is not None and len(merged) < len(previous) * 0.99:
                    issues.append(
                        QualityIssue(step, "history_not_shrunk", "blocking",
                                     f"{item.symbol} 历史从 {len(previous)} 行缩减到 {len(merged)} 行",
                                     symbol=item.symbol)
                    )
                ctx.issues.extend(issues)
                if has_blocking(issues):
                    self._quarantine(ctx, step, f"symbol={item.symbol}", incoming)
                    ctx.count(step, "quarantined")
                    continue
                write_canonical_frame(self.root, step, merged, partition=f"symbol={item.symbol}")
                ctx.count(step, "updated")
            except Exception as exc:
                self.logger.warning("Index %s failed: %s", item.symbol, exc)
                ctx.error(step, item.symbol, exc)
                ctx.count(step, "failed")
                ctx.issues.append(
                    QualityIssue(step, "ingestion_success", "warning", f"{item.symbol} 指数下载失败: {exc}",
                                 symbol=item.symbol)
                )

    # ------------------------------------------------------ status history

    def _ingest_status_history(self, ctx: RunContext) -> None:
        self._ingest_suspensions(ctx)
        self._ingest_risk_warnings(ctx)

    def _ingest_suspensions(self, ctx: RunContext) -> None:
        step = "suspension_events"
        assert ctx.expected_latest is not None
        events = read_canonical(self.root, step)
        log = read_canonical(self.root, "suspension_fetch_log")
        new_parts: list[pd.DataFrame] = []
        log_rows: list[dict[str, Any]] = []
        try:
            raw = self.provider.fetch_suspension_snapshot(self._date_str(ctx.expected_latest))
            self._raw(ctx, "suspensions_tfp", self._date_str(ctx.expected_latest), raw,
                      "akshare.stock_tfp_em.eastmoney")
            new_parts.append(normalize_tfp_suspensions(raw, ctx.expected_latest, ctx.run_id, ctx.ingested_at))
            ctx.count(step, "tfp_snapshots")
        except Exception as exc:
            ctx.error(step, "stock_tfp_em", exc)
            ctx.issues.append(QualityIssue(step, "tfp_snapshot", "warning", f"停复牌快照下载失败: {exc}"))
        backfill_start = pd.to_datetime(self.config.suspension_backfill_start).date()
        done = set()
        if log is not None and not log.empty:
            done = set(log.loc[log["source"] == "baidu", "query_date"])
        # Re-query the latest sessions: announcements can land after the day.
        recent = set(ctx.open_dates[max(0, ctx.open_dates.index(ctx.expected_latest) - 2):
                                    ctx.open_dates.index(ctx.expected_latest) + 1])
        dates = [d for d in ctx.open_dates
                 if backfill_start <= d <= ctx.expected_latest and (d not in done or d in recent)]
        consecutive_failures = 0
        for index, query_date in enumerate(dates, start=1):
            try:
                raw = self.provider.fetch_suspension_events(self._date_str(query_date))
                self._raw(ctx, "suspensions_baidu", self._date_str(query_date), raw,
                          "akshare.news_trade_notify_suspend_baidu")
                part = normalize_baidu_suspensions(raw, query_date, ctx.run_id, ctx.ingested_at)
                new_parts.append(part)
                log_rows.append({"source": "baidu", "query_date": query_date, "rows": len(part),
                                 "run_id": ctx.run_id})
                consecutive_failures = 0
                ctx.count(step, "baidu_days")
            except Exception as exc:
                consecutive_failures += 1
                ctx.error(step, f"baidu:{query_date}", exc)
                if consecutive_failures >= BAIDU_MAX_CONSECUTIVE_FAILURES:
                    ctx.issues.append(
                        QualityIssue(step, "baidu_backfill", "warning",
                                     f"百度停复牌连续失败 {consecutive_failures} 次，剩余 "
                                     f"{len(dates) - index} 个交易日留待下次运行: {exc}")
                    )
                    break
            if index % 100 == 0:
                self.logger.info("Baidu suspension calendar progress: %s/%s", index, len(dates))
        merged = events
        for part in new_parts:
            merged = merge_suspension_events(merged, part)
        if merged is not None and not merged.empty:
            write_canonical_frame(self.root, step, merged)
            ctx.summaries[step] = {"events": int(len(merged))}
        if log_rows:
            new_log = pd.DataFrame(log_rows)
            if log is not None and not log.empty:
                new_log = pd.concat([log, new_log], ignore_index=True).drop_duplicates(
                    ["source", "query_date"], keep="last")
            write_canonical_frame(self.root, "suspension_fetch_log", new_log)

    def _ingest_risk_warnings(self, ctx: RunContext) -> None:
        assert ctx.master is not None
        try:
            raw = self.provider.fetch_sz_name_changes()
            self._raw(ctx, "security_name_changes", "szse", raw, "akshare.stock_info_sz_change_name")
            changes = normalize_sz_name_changes(raw, ctx.run_id, ctx.ingested_at)
            if not changes.empty:
                write_canonical_frame(self.root, "security_name_changes", changes)
        except Exception as exc:
            ctx.error("security_name_changes", "szse", exc)
            ctx.issues.append(QualityIssue("security_name_changes", "download", "warning",
                                           f"深交所简称变更下载失败，沿用上一版本: {exc}"))
            changes = read_canonical(self.root, "security_name_changes")
        intervals = build_risk_warning_intervals(changes, ctx.master, ctx.run_id)
        if not intervals.empty:
            write_canonical_frame(self.root, "risk_warning_intervals", intervals)
            ctx.summaries["risk_warning_intervals"] = {
                str(key): int(value) for key, value in intervals["method"].value_counts().items()
            }

    # ------------------------------------------------------------ snapshot

    def _ingest_market_snapshot(self, ctx: RunContext) -> None:
        step = "market_snapshot"
        try:
            result = self.provider.fetch_market_snapshot()
            fetched_at = self.clock()
            session, kind = snapshot_session(ctx.open_dates, fetched_at, self.final_time)
            # The session label is part of the raw name so a rebuild can
            # reproduce the canonical partition.
            self._raw(ctx, step, f"all_a_shares_{session or 'intraday'}", result.frame, result.source)
            if session is None:
                ctx.issues.append(
                    QualityIssue(step, "end_of_day_only", "warning",
                                 f"快照抓取于盘中 {fetched_at.isoformat()}，不写入标准层")
                )
                ctx.count(step, "skipped_intraday")
                return
            snapshot = normalize_market_snapshot(
                result.frame, session, ctx.run_id, ctx.ingested_at, source=result.source,
                fetched_at=fetched_at.isoformat(),
            )
            write_canonical_frame(self.root, step, snapshot, partition=f"snapshot_date={session}")
            ctx.count(step, "written")
            ctx.summaries[step] = {"session": str(session), "kind": kind, "rows": int(len(snapshot))}
        except Exception as exc:
            ctx.error(step, "all", exc)
            ctx.issues.append(QualityIssue(step, "ingestion_success", "warning", f"全市场快照下载失败: {exc}"))

    # --------------------------------------------------------------- audit

    def _audit(self, ctx: RunContext) -> None:
        assert ctx.expected_latest is not None
        result = run_audit(self.root, ctx.expected_latest, ctx.start_date,
                           self.config.min_latest_coverage, ctx.run_id)
        ctx.issues.extend(result.issues)
        ctx.summaries["audit"] = result.summary
        if not result.gaps.empty:
            write_canonical_frame(self.root, "bar_gaps", result.gaps)

    # ------------------------------------------------------------ finalize

    def _finalize(self, ctx: RunContext, manifest_path: Path, fatal: BaseException | None) -> dict[str, Any]:
        summary = quality_summary(ctx.issues)
        daily = ctx.counters.get("daily_bars", {})
        usable = daily.get("updated", 0) + daily.get("frozen_delisted", 0)
        if fatal is not None or usable == 0:
            status = "failed"
        elif summary["blocking"]:
            status = "partial"
        else:
            status = "complete"
        sidecar_dir = self.root / "data" / "manifests" / f"run_id={ctx.run_id}"
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        (sidecar_dir / "raw_files.txt").write_text("\n".join(ctx.raw_files) + "\n", encoding="utf-8")
        inventory, data_version = canonical_inventory(self.root)
        write_parquet_atomic(inventory, sidecar_dir / "partitions.parquet")
        datasets = dataset_summary(inventory)
        report_json, report_markdown, issues_path = write_quality_report(
            self.root, ctx.run_id, ctx.issues, datasets, ctx.counters, ctx.summaries
        )
        manifest: dict[str, Any] = {
            "run_id": ctx.run_id,
            "started_at": ctx.started_at,
            "finished_at": utc_now_iso(),
            "status": status,
            "mode": ctx.mode,
            "provider": self.config.provider,
            "provider_version": self.provider.version,
            "code_version": code_version(self.root),
            "config_hash": self.config.config_hash,
            "start_date": str(ctx.start_date),
            "expected_latest_date": str(ctx.expected_latest) if ctx.expected_latest else None,
            "data_version": data_version,
            "quality_summary": summary,
            "counters": ctx.counters,
            "summaries": ctx.summaries,
            "datasets": datasets,
            "errors": ctx.errors,
            "fatal_error": str(fatal) if fatal else None,
            "raw_file_count": len(ctx.raw_files),
            "sidecars": {
                "raw_files": (sidecar_dir / "raw_files.txt").relative_to(self.root).as_posix(),
                "partitions": (sidecar_dir / "partitions.parquet").relative_to(self.root).as_posix(),
                "issues": issues_path.relative_to(self.root).as_posix(),
            },
            "quality_report_json": report_json.relative_to(self.root).as_posix(),
            "quality_report_markdown": report_markdown.relative_to(self.root).as_posix(),
        }
        json_dump(manifest_path, manifest)
        try:
            manifest["database_path"] = str(build_duckdb_catalog(self.root))
            manifest["catalog_status"] = "built"
        except CatalogError as exc:
            self.logger.error("%s", exc)
            manifest["catalog_status"] = f"failed: {exc}"
            if manifest["status"] == "complete":
                manifest["status"] = "partial"
        json_dump(manifest_path, manifest)
        self.logger.info(
            "Ingestion run %s finished with status=%s (blocking=%s, warning=%s)",
            ctx.run_id, manifest["status"], summary["blocking"], summary["warning"],
        )
        return manifest

