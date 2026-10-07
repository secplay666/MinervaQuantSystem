from __future__ import annotations

import bisect
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import run_audit
from .config import DataPlatformConfig
from .etf import (
    SOURCE_ETF_LIST_SSE,
    SOURCE_ETF_LIST_SZSE,
    SOURCE_ETF_SSE,
    SOURCE_ETF_SZSE,
    etf_fetch_log_row,
    etf_prefix,
    group_members,
    load_etf_groups,
    merge_etf_master,
    merge_etf_shares,
    normalize_etf_bars,
    normalize_etf_list_sse,
    normalize_etf_list_szse,
    normalize_etf_sse,
    normalize_etf_szse,
    sse_etf_dates,
    sse_window,
    szse_etf_months,
    szse_window,
)
from .etf_holders import SOURCE_FUND_REPORT, holder_rows, lists_to_refresh, merge_holders, reports_to_fetch
from .intraday import BAR_COLUMNS as INTRADAY_BAR_COLUMNS
from .intraday import SOURCE_BARS, SOURCE_FUTURES, SOURCE_TRADES
from .intraday import TRADE_COLUMNS as INTRADAY_TRADE_COLUMNS
from .intraday import check_trades as check_intraday_trades
from .intraday import has_trades as intraday_has_trades
from .intraday import merge_day as merge_intraday_day
from .intraday import normalize_bars as normalize_intraday_bars
from .intraday import normalize_trades as normalize_intraday_trades
from .intraday import symbol_of as intraday_symbol
from .financials import (
    SOURCE_FINANCIALS,
    STATEMENTS,
    financial_windows,
    merge_financial_versions,
    normalize_financials,
)
from .forecasts import DATASET as FORECAST_DATASET
from .forecasts import SOURCE_FORECASTS, forecast_windows, merge_forecasts, normalize_forecasts
from .corporate import (
    SOURCE_DIVIDENDS,
    SOURCE_INDEX_WEIGHTS,
    SOURCE_SHARE_CAPITAL,
    SOURCE_SW,
    dividend_report_dates,
    dividend_window,
    load_sw2014_mapping,
    merge_dividends,
    merge_fetch_log,
    merge_index_weights,
    merge_share_capital,
    normalize_dividends,
    normalize_index_weights,
    normalize_share_capital,
    normalize_sw_classification,
    same_frame,
    share_capital_windows,
)
from .normalization import (
    SCHEMA_VERSION,
    as_date,
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
from .risk_history import (
    RISK_KEYWORDS,
    bulletin_windows,
    derive_risk_intervals,
    merge_bulletins,
    normalize_bse_announcements,
    normalize_sse_bulletins,
)
from .sessions import (
    SHANGHAI_TZ,
    latest_final_session,
    parse_hhmm,
    session_offset,
    shanghai_now,
    snapshot_session,
)
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
from .sw_index import SW_L1
from .utils import code_version, ensure_directories, json_dump, run_id_to_iso, unique_run_id, utc_now_iso

MAX_RECORDED_ERRORS = 200
STEPS = ("daily_bars", "adjustment_factors", "indices", "status_history", "market_snapshot", "corporate",
         "classification", "fundamentals", "etf", "intraday")
# Run only when asked (``--steps``): the one-off backfill of history before
# the earliest stored bar, e.g. after start_date moved earlier.
OPTIONAL_STEPS = ("bars_history",)
ALL_STEPS = STEPS + OPTIONAL_STEPS
CORPORATE_MAX_CONSECUTIVE_FAILURES = 3
ETF_MAX_CONSECUTIVE_FAILURES = 2
# A delisted symbol with no bars at all that left the market this long ago is
# history to backfill (bars_history), not something the daily run fetches.
HISTORY_ONLY_DELISTED_DAYS = 365
HISTORY_FINAL_STATUSES = ("done", "no_data", "quarantined")
HISTORY_LOG_COLUMNS = ["symbol", "window_start", "head_end", "status", "rows", "first_date", "detail", "run_id"]
BAIDU_MAX_CONSECUTIVE_FAILURES = 10
BULLETIN_MAX_CONSECUTIVE_FAILURES = 2


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
        self.steps: set[str] = set(STEPS)

    # ------------------------------------------------------------------ run

    def run(self, mode: str = "incremental", steps: set[str] | None = None) -> dict[str, Any]:
        """``steps`` limits the run to some of STEPS (calendar, security
        master, audit and finalization always run)."""
        if mode not in {"full", "incremental"}:
            raise ValueError("mode must be one of: full, incremental")
        unknown = set(steps or ()) - set(ALL_STEPS)
        if unknown:
            raise ValueError(f"unknown steps {sorted(unknown)}; choose from {list(ALL_STEPS)}")
        self.steps = set(steps) if steps else set(STEPS)
        ensure_directories(self.root)
        run_id = unique_run_id(self.root)
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
        except BaseException as exc:  # Ctrl+C / SystemExit: record, then propagate
            json_dump(manifest_path, {"run_id": run_id, "status": "aborted", "mode": mode,
                                      "started_at": ctx.started_at, "finished_at": utc_now_iso(),
                                      "fatal_error": f"{type(exc).__name__}: {exc}",
                                      "counters": ctx.counters})
            raise
        return self._finalize(ctx, manifest_path, fatal)

    def _run_steps(self, ctx: RunContext) -> None:
        self._ingest_calendar(ctx)
        self._ingest_security_master(ctx)
        universe = self._daily_universe(ctx)
        if "daily_bars" in self.steps:
            self._ingest_daily_bars(ctx, universe)
        if "bars_history" in self.steps:
            self._ingest_bars_history(ctx, universe)
        if "adjustment_factors" in self.steps and self.config.download_adjustment_factors:
            self._ingest_adjustment_factors(ctx, universe)
        if "indices" in self.steps:
            self._ingest_indices(ctx)
            if self.config.download_sw_indices:
                self._ingest_sw_indices(ctx)
        if "status_history" in self.steps and self.config.download_status_history:
            self._ingest_status_history(ctx)
        if "market_snapshot" in self.steps:
            self._ingest_market_snapshot(ctx)
        if "corporate" in self.steps and self.config.download_corporate:
            self._ingest_corporate(ctx)
        if "classification" in self.steps and self.config.download_classification:
            self._ingest_classification(ctx)
        if "fundamentals" in self.steps and self.config.download_fundamentals:
            self._ingest_fundamentals(ctx)
        if "etf" in self.steps and self.config.download_etf:
            self._ingest_etf(ctx)
        if "intraday" in self.steps and self.config.download_intraday:
            self._ingest_intraday(ctx)
        self._audit(ctx, universe)

    # ------------------------------------------------------------ helpers

    def _raw(self, ctx: RunContext, dataset: str, name: str, frame: pd.DataFrame,
             source: str | None = None) -> Path:
        path = write_raw_frame(self.root, self.config.provider, dataset, ctx.run_id, name, frame, source)
        ctx.raw_files.append(path.relative_to(self.root).as_posix())
        return path

    def _reject_raw(self, ctx: RunContext, path: Path) -> None:
        """Move this run's raw copy of a rejected response to ``<dataset>_rejected``
        so a rebuild does not replay what ingestion refused."""
        run_dir = path.parent
        target = run_dir.parent.parent / f"{run_dir.parent.name}_rejected" / run_dir.name / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
        old, new = path.relative_to(self.root).as_posix(), target.relative_to(self.root).as_posix()
        ctx.raw_files = [new if item == old else item for item in ctx.raw_files]

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
        try:
            lists = self.provider.fetch_security_lists()
        except Exception as exc:
            previous = read_canonical(self.root, "security_master")
            if previous is None:
                raise
            # A temporary outage (e.g. exchange anti-crawling) must not stop
            # the day's bars; yesterday's master misses at most new listings.
            ctx.error("security_master", "exchange_lists", exc)
            ctx.issues.append(QualityIssue("security_master", "lists_available", "warning",
                                           f"交易所证券列表暂时无法获取，沿用上一版证券主数据: {exc}"))
            ctx.master = previous
            return
        for name, frame in lists.items():
            self._raw(ctx, "security_lists", name, frame, f"akshare.security_lists.{name}")
        manual = [asdict(item) for item in self.config.manual_delistings]
        master = normalize_security_master(lists, manual, ctx.run_id, ctx.ingested_at)
        previous = self._previous_master()
        issues = validate_security_master(master, previous, self.config.max_listing_shrink_ratio)
        empty_lists = sorted(name for name, frame in lists.items() if frame is None or frame.empty)
        if empty_lists:
            # Every list (including the delisting lists) is always non-empty
            # upstream; an empty one means a failed or truncated response.
            issues.append(
                QualityIssue("security_master", "lists_not_empty", "blocking",
                             f"交易所列表返回为空: {', '.join(empty_lists)}", len(empty_lists))
            )
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
            try:
                os.rename(legacy, target)
            except OSError as exc:  # e.g. a file held open on Windows
                ctx.issues.append(QualityIssue("security_master", "legacy_instruments_archived", "warning",
                                               f"旧 instruments 目录归档失败，保留原处: {exc}"))
        ctx.master = master
        counts = master["status"].value_counts().to_dict()
        ctx.summaries["security_master"] = {key: int(value) for key, value in counts.items()}

    def _carry_forward_removed(
        self, ctx: RunContext, master: pd.DataFrame, previous: pd.DataFrame
    ) -> pd.DataFrame:
        """Keep every previously known security that is missing now.

        A master row is never dropped: that would reintroduce survivorship
        bias.  Previously delisted rows are kept as they were.  A live code
        that vanishes without a delisting record (BSE publishes none) is kept
        as delisted with an unknown date and flagged for review.
        """
        missing = previous[~previous["symbol"].isin(master["symbol"])]
        kept = missing[missing["status"] == "delisted"]
        if not kept.empty:
            ctx.issues.append(
                QualityIssue(
                    "security_master",
                    "delisted_record_missing",
                    "warning",
                    "以下已退市证券不再出现在退市列表中，沿用上一版本记录: " + ", ".join(kept["symbol"].head(50)),
                    int(len(kept)),
                )
            )
            master = pd.concat([master, kept.reindex(columns=master.columns)], ignore_index=True)
        removed = missing[missing["status"] != "delisted"]
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
        self, ctx: RunContext, window_start: date, bounds: tuple[date, date, date | None] | None
    ) -> date:
        if ctx.mode == "full" or bounds is None:
            return window_start
        # History before the first stored bar (start_date moved earlier, or
        # no trading on the first sessions) is the bars_history step's job.
        _, existing_max, _ = bounds
        # Re-fetch a few sessions so late vendor revisions replace old rows.
        overlap_start = session_offset(ctx.open_dates, existing_max, self.config.overlap_sessions)
        return max(window_start, overlap_start)

    def _partition_bounds(self, dataset: str, symbol: str) -> tuple[date, date, date | None] | None:
        """(first bar, last bar, Shanghai date of the latest ingestion)."""
        path = canonical_path(self.root, dataset, f"symbol={symbol}")
        if not path.exists():
            return None
        frame = pd.read_parquet(path, columns=["trade_date", "ingested_at"])
        if frame.empty:
            return None
        ingested = pd.to_datetime(frame["ingested_at"], errors="coerce", utc=True).max()
        ingested_date = None if pd.isna(ingested) else ingested.tz_convert(SHANGHAI_TZ).date()
        return frame["trade_date"].min(), frame["trade_date"].max(), ingested_date

    @staticmethod
    def _delisted_history_final(
        ctx: RunContext, item: UniverseItem, last_date: date | None, fetched_on: date | None
    ) -> bool:
        """A delisted symbol's history is final, so it need not be fetched again.

        Final when it already reaches the last session before delisting, or
        was fetched on/after the delisting date (covers names whose trading
        stopped weeks earlier, e.g. merger suspensions).
        """
        if not item.is_delisted or item.delist_date is None:
            return False
        before = [d for d in ctx.open_dates if d < item.delist_date]
        if last_date is not None and before and last_date >= before[-1]:
            return True
        return fetched_on is not None and fetched_on >= item.delist_date

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
            if ctx.mode == "incremental" and bounds is None and self._history_only(ctx, item):
                ctx.count(step, "history_pending")
                continue
            if (ctx.mode == "incremental" and bounds is not None
                    and self._delisted_history_final(ctx, item, bounds[1], bounds[2])):
                ctx.count(step, "frozen_delisted")
                continue
            pending[item.symbol] = (item, self._fetch_start(ctx, window_start, bounds), window_end)
        ctx.count(step, "requested", len(pending))

        def fetch(symbol: str) -> FetchResult:
            item, fetch_start, window_end = pending[symbol]
            return self.provider.fetch_daily_bars(
                symbol, self._date_str(fetch_start), self._date_str(window_end), delisted=item.is_delisted
            )

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {executor.submit(fetch, symbol): symbol for symbol in pending}
            try:
                for done, future in enumerate(as_completed(futures), start=1):
                    symbol = futures[future]
                    item, fetch_start, window_end = pending[symbol]
                    try:
                        result = future.result()
                        previous = read_canonical(self.root, step, f"symbol={symbol}")
                        existing = previous if ctx.mode == "incremental" else None
                        status = self._store_daily_bars(ctx, item, fetch_start, window_end, existing,
                                                        result, previous)
                        checkpoint["completed"].append({"symbol": symbol, "status": status})
                    except Exception as exc:
                        self.logger.warning("Daily bars failed for %s: %s", symbol, exc)
                        ctx.error(step, symbol, exc)
                        ctx.count(step, "failed")
                        checkpoint["failed"].append({"symbol": symbol, "error": str(exc)})
                    if done % 100 == 0 or done == len(futures):
                        self.logger.info("Daily bars progress: %s/%s", done, len(futures))
                        self._checkpoint(ctx, step, checkpoint)
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
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
        previous: pd.DataFrame | None = None,
    ) -> str:
        step = "daily_bars"
        symbol = item.symbol
        raw_path = self._raw(ctx, step, symbol, result.frame, result.source)
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
        if previous is not None and not previous.empty:
            # A full reload must not lose sessions we already hold (truncated
            # vendor responses); incremental merges are supersets by design.
            held, _ = clip_bars(previous[["trade_date"]], window_start, window_end)
            lost = sorted(set(held["trade_date"]) - set(merged["trade_date"]))
            if lost:
                issues.append(
                    QualityIssue(step, "history_not_shrunk", "blocking",
                                 f"{symbol} 新数据缺少已有的 {len(lost)} 个交易日（最早 {lost[0]}）",
                                 len(lost), symbol=symbol)
                )
        ctx.issues.extend(issues)
        if has_blocking(issues):
            self._quarantine(ctx, step, f"symbol={symbol}", merged)
            self._reject_raw(ctx, raw_path)
            ctx.count(step, "quarantined")
            return "quarantined"
        write_canonical_frame(self.root, step, merged, partition=f"symbol={symbol}")
        ctx.first_bar_dates[symbol] = merged["trade_date"].min()
        ctx.count(step, "updated")
        if incoming.empty and existing is not None and not existing.empty:
            ctx.count(step, "no_new_rows")
        return "updated"

    @staticmethod
    def _history_only(ctx: RunContext, item: UniverseItem) -> bool:
        assert ctx.expected_latest is not None
        return (item.is_delisted and item.delist_date is not None
                and item.delist_date < ctx.expected_latest - timedelta(days=HISTORY_ONLY_DELISTED_DAYS))

    # ------------------------------------------------------ bars history

    def _ingest_bars_history(self, ctx: RunContext, universe: list[UniverseItem]) -> None:
        """Backfill bars before each symbol's first stored bar, and whole
        histories of long-delisted symbols that have none.

        Only the missing head is fetched and it never replaces stored rows.
        An accepted head is kept as raw ``daily_bars`` (so a rebuild replays
        it); a head failing validation is kept as raw
        ``daily_bars_history_rejected`` plus a quarantine copy and reported
        as a warning, so old vendor data cannot make daily runs partial.
        The log records final outcomes per (symbol, window start).
        """
        step = "bars_history"
        log = read_canonical(self.root, "daily_bars_history_log")
        final = set()
        if log is not None and not log.empty:
            for row in log[log["status"].isin(HISTORY_FINAL_STATUSES)].itertuples(index=False):
                final.add((row.symbol, str(row.window_start)))
        jobs: dict[str, tuple[UniverseItem, date, date, date | None]] = {}
        sessions = ctx.open_dates
        for item in universe:
            window_start, window_end = self._bar_window(ctx, item)
            at = bisect.bisect_left(sessions, window_start)
            if at == len(sessions) or sessions[at] > window_end:
                continue
            first_session = sessions[at]
            if (item.symbol, str(window_start)) in final:
                ctx.count(step, "logged")
                continue
            bounds = self._partition_bounds("daily_bars", item.symbol)
            if bounds is None:
                if not item.is_delisted:
                    ctx.count(step, "no_partition_live")  # the daily step's job
                    continue
                jobs[item.symbol] = (item, window_start, window_end, None)
            elif bounds[0] > first_session:
                head_end = sessions[bisect.bisect_left(sessions, bounds[0]) - 1]
                jobs[item.symbol] = (item, window_start, head_end, bounds[0])
            else:
                ctx.count(step, "complete")
        ctx.count(step, "requested", len(jobs))
        self.logger.info("Bars history: %s heads to fetch", len(jobs))
        log_rows: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal log
            if log_rows:
                parts = [part for part in (log, pd.DataFrame(log_rows, columns=HISTORY_LOG_COLUMNS))
                         if part is not None and not part.empty]
                log = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol", "window_start"], keep="last")
                write_canonical_frame(self.root, "daily_bars_history_log",
                                      log.sort_values(["symbol", "window_start"], ignore_index=True))
                log_rows.clear()

        def fetch(symbol: str) -> FetchResult:
            item, head_start, head_end, _ = jobs[symbol]
            return self.provider.fetch_daily_bars(symbol, self._date_str(head_start), self._date_str(head_end),
                                                  delisted=item.is_delisted)

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {executor.submit(fetch, symbol): symbol for symbol in jobs}
            try:
                for done, future in enumerate(as_completed(futures), start=1):
                    symbol = futures[future]
                    item, head_start, head_end, _ = jobs[symbol]
                    try:
                        status, rows, first, detail = self._store_bars_head(ctx, item, head_start, head_end,
                                                                            future.result())
                    except Exception as exc:
                        self.logger.warning("Bars history failed for %s: %s", symbol, exc)
                        ctx.error(step, symbol, exc)
                        ctx.count(step, "failed")
                        continue
                    ctx.count(step, status)
                    log_rows.append({"symbol": symbol, "window_start": head_start, "head_end": head_end,
                                     "status": status, "rows": rows, "first_date": first, "detail": detail,
                                     "run_id": ctx.run_id})
                    if done % 200 == 0 or done == len(futures):
                        self.logger.info("Bars history progress: %s/%s", done, len(futures))
                        flush()
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                flush()
                raise
        flush()
        counters = ctx.counters.get(step, {})
        if counters.get("failed"):
            ctx.issues.append(QualityIssue(
                step, "fetch_failed", "warning",
                f"{counters['failed']} 只证券的历史日线下载失败，下次回补运行重试", counters["failed"]))
        if counters.get("no_data"):
            ctx.issues.append(QualityIssue(
                step, "no_data", "warning",
                f"{counters['no_data']} 只证券在回补区间内数据源没有日线（已记录，不再重复请求）", counters["no_data"]))

    def _store_bars_head(
        self, ctx: RunContext, item: UniverseItem, head_start: date, head_end: date, result: FetchResult
    ) -> tuple[str, int, date | None, str]:
        """(status, rows, first date, detail) for one fetched head."""
        symbol = item.symbol
        incoming = normalize_daily_bars(result.frame, symbol, ctx.run_id, ctx.ingested_at, result.source)
        incoming, dropped = drop_invalid_price_rows(incoming)
        if dropped:
            ctx.count("bars_history", "invalid_price_rows_dropped", dropped)
        head, _ = clip_bars(incoming, head_start, head_end)
        if head.empty:
            self._raw(ctx, "daily_bars", symbol, result.frame, result.source)
            return "no_data", 0, None, ""
        existing = read_canonical(self.root, "daily_bars", f"symbol={symbol}")
        merged = merge_daily_bars(existing, head)
        issues = validate_bars(merged, "daily_bars", symbol) + validate_volume_units(merged, symbol)
        if has_blocking(issues):
            self._raw(ctx, "daily_bars_history_rejected", symbol, result.frame, result.source)
            self._quarantine(ctx, "daily_bars_history", f"symbol={symbol}", head)
            rules = sorted({issue.rule for issue in issues if issue.severity == "blocking"})
            ctx.issues.extend(replace(issue, dataset="bars_history", severity="warning",
                                      message=f"历史回补未采用：{issue.message}")
                              for issue in issues if issue.severity == "blocking")
            return "quarantined", len(head), head["trade_date"].min(), ",".join(rules)
        self._raw(ctx, "daily_bars", symbol, result.frame, result.source)
        ctx.issues.extend(issues)
        write_canonical_frame(self.root, "daily_bars", merged, partition=f"symbol={symbol}")
        ctx.first_bar_dates[symbol] = merged["trade_date"].min()
        return "done", len(head), head["trade_date"].min(), ""

    # ------------------------------------------------- adjustment factors

    def _ingest_adjustment_factors(self, ctx: RunContext, universe: list[UniverseItem]) -> None:
        step = "adjustment_factors"
        pending: list[UniverseItem] = []
        for item in universe:
            # Factors are re-anchored snapshots, not an append-only log, so
            # every live symbol is refreshed each run; delisted ones become
            # final once refreshed with factor_as_of on/after the delisting.
            if ctx.mode == "incremental" and self._delisted_history_final(
                    ctx, item, None, self._factor_as_of(item.symbol)):
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
            try:
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
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
        self._checkpoint(ctx, step, checkpoint)
        refreshed = ctx.counters.get(step, {}).get("refreshed", 0)
        if pending and refreshed / len(pending) < self.config.min_factor_success_ratio:
            ctx.issues.append(
                QualityIssue(step, "refresh_success_ratio", "blocking",
                             f"仅 {refreshed}/{len(pending)} 只证券复权因子刷新成功", len(pending) - refreshed)
            )

    def _factor_as_of(self, symbol: str) -> date | None:
        path = canonical_path(self.root, "adjustment_factors", f"symbol={symbol}")
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        if "factor_as_of" not in frame.columns:
            return None  # legacy partition: refresh once
        return as_date(frame["factor_as_of"].dropna().max()) if frame["factor_as_of"].notna().any() else None

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
                fetch = (self.provider.fetch_csindex_daily if item.source == "csindex"
                         else self.provider.fetch_index_daily)
                result = fetch(item.symbol, self._date_str(fetch_start), self._date_str(ctx.expected_latest))
                raw_path = self._raw(ctx, step, item.symbol, result.frame, result.source)
                incoming = normalize_index_bars(
                    result.frame, item.symbol, item.name, index_start, ctx.expected_latest,
                    ctx.run_id, ctx.ingested_at, source=result.source,
                )
                if item.source == "csindex":
                    # CSIndex prepends a placeholder row (1990-01-01, base value) that is no session.
                    sessions = set(ctx.open_dates)
                    incoming = incoming[incoming["trade_date"].isin(sessions)].reset_index(drop=True)
                issues = validate_index_refresh(incoming, existing, item.symbol)
                merged = merge_index_bars(existing, incoming)
                if item.source == "csindex":
                    merged = merged[merged["trade_date"].isin(sessions)].reset_index(drop=True)
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
                    self._reject_raw(ctx, raw_path)
                    ctx.count(step, "quarantined")
                    continue
                write_canonical_frame(self.root, step, merged, partition=f"symbol={item.symbol}")
                ctx.count(step, "updated")
                if merged["trade_date"].max() < ctx.expected_latest:
                    ctx.issues.append(
                        QualityIssue(step, "latest_trade_date", "warning",
                                     f"{item.symbol} 最新指数日期 {merged['trade_date'].max()} 早于 "
                                     f"{ctx.expected_latest}", symbol=item.symbol)
                    )
            except Exception as exc:
                self.logger.warning("Index %s failed: %s", item.symbol, exc)
                ctx.error(step, item.symbol, exc)
                ctx.count(step, "failed")
                ctx.issues.append(
                    QualityIssue(step, "ingestion_success", "warning", f"{item.symbol} 指数下载失败: {exc}",
                                 symbol=item.symbol)
                )

    # ------------------------------------------------------ status history

    def _ingest_sw_indices(self, ctx: RunContext) -> None:
        """The 31 SW L1 industry indices behind the money map.  Each call
        returns an index's whole history, merged into what is stored.  A
        refresh that fails the bar checks is kept aside and the stored
        history stays; problems are warnings (the data feeds a map, not
        decisions)."""
        assert ctx.expected_latest is not None
        step = "sw_index_bars"
        start = pd.to_datetime(self.config.sw_index_start).date()
        for code, name in SW_L1.items():
            try:
                result = self.provider.fetch_sw_index_daily(code)
            except NotImplementedError:
                return
            except Exception as exc:
                ctx.error(step, code, exc)
                ctx.count(step, "failed")
                continue
            incoming = normalize_index_bars(result.frame, code, name, start, ctx.expected_latest, ctx.run_id,
                                            ctx.ingested_at, source=result.source)
            existing = read_canonical(self.root, step, f"symbol={code}")
            issues = validate_index_refresh(incoming, existing, code)
            if has_blocking(issues):
                # Kept aside so a rebuild does not replay it.
                self._raw(ctx, f"{step}_rejected", code, result.frame, result.source)
                ctx.issues.extend(replace(issue, dataset=step, severity="warning") for issue in issues)
                ctx.count(step, "rejected")
                continue
            self._raw(ctx, step, code, result.frame, result.source)
            write_canonical_frame(self.root, step, merge_index_bars(existing, incoming), partition=f"symbol={code}")
            ctx.count(step, "updated")
            if incoming["trade_date"].max() < ctx.expected_latest:
                ctx.count(step, "behind")
        failed = sum(ctx.counters.get(step, {}).get(key, 0) for key in ("failed", "rejected"))
        behind = ctx.counters.get(step, {}).get("behind", 0)
        if failed or behind:
            ctx.issues.append(QualityIssue(step, "fetch", "warning",
                                           f"申万行业指数：{failed} 个下载失败或被拒，{behind} 个还没有 "
                                           f"{ctx.expected_latest} 的数据", failed + behind))

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
        done, empty_before = set(), set()
        if log is not None and not log.empty:
            baidu = log[log["source"] == "baidu"]
            done = set(baidu.loc[baidu["rows"] >= 0, "query_date"])
            empty_before = set(baidu.loc[baidu["rows"] < 0, "query_date"])
        # Re-query the latest sessions: announcements can land after the day.
        recent = set(ctx.open_dates[max(0, ctx.open_dates.index(ctx.expected_latest) - 2):
                                    ctx.open_dates.index(ctx.expected_latest) + 1])
        dates = [d for d in ctx.open_dates
                 if backfill_start <= d <= ctx.expected_latest and (d not in done or d in recent)]
        consecutive_failures = failures = empty_days = 0
        for index, query_date in enumerate(dates, start=1):
            try:
                raw = self.provider.fetch_suspension_events(self._date_str(query_date))
                self._raw(ctx, "suspensions_baidu", self._date_str(query_date), raw,
                          "akshare.news_trade_notify_suspend_baidu")
                consecutive_failures = 0
                if raw is None or len(raw.columns) == 0:
                    # AKShare returns a bare DataFrame() both when throttled and
                    # on a day without events.  A day is retried once in a later
                    # run; empty twice, it is recorded as a day without events.
                    confirmed = query_date in empty_before
                    log_rows.append({"source": "baidu", "query_date": query_date, "rows": 0 if confirmed else -1,
                                     "run_id": ctx.run_id})
                    ctx.count(step, "baidu_empty_confirmed" if confirmed else "baidu_empty_days")
                    if not confirmed:
                        empty_days += 1
                    continue
                part = normalize_baidu_suspensions(raw, query_date, ctx.run_id, ctx.ingested_at)
                new_parts.append(part)
                log_rows.append({"source": "baidu", "query_date": query_date, "rows": len(part),
                                 "run_id": ctx.run_id})
                ctx.count(step, "baidu_days")
            except Exception as exc:
                consecutive_failures += 1
                failures += 1
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
        if failures and consecutive_failures < BAIDU_MAX_CONSECUTIVE_FAILURES:
            ctx.issues.append(QualityIssue(step, "baidu_backfill", "warning",
                                           f"百度停复牌 {failures}/{len(dates)} 个交易日下载失败，下次运行重试", failures))
        if empty_days:
            ctx.issues.append(
                QualityIssue(step, "baidu_empty_response", "warning",
                             f"百度停复牌 {empty_days}/{len(dates)} 个交易日返回空结果（可能被限流），下次运行重试",
                             empty_days)
            )
        merged = events
        for part in new_parts:
            merged = merge_suspension_events(merged, part)
        if merged is not None and ctx.master is not None:
            # Baidu tags some NEEQ companies as BJ; keep exchange securities only.
            merged = merged[merged["symbol"].isin(ctx.master["symbol"])]
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
        bulletins = self._ingest_risk_bulletins(ctx)
        sessions = [day.isoformat() for day in ctx.open_dates]
        combined = derive_risk_intervals(self.root, sessions, ctx.master, changes, bulletins, ctx.run_id,
                                         SCHEMA_VERSION, str(ctx.expected_latest))
        if not combined.intervals.empty:
            write_canonical_frame(self.root, "risk_warning_intervals", combined.intervals)
        if not combined.adjustments.empty:
            write_canonical_frame(self.root, "risk_warning_adjustments", combined.adjustments)
        ctx.summaries["risk_warning_intervals"] = {
            "by_method": {str(k): int(v) for k, v in combined.intervals["source"].value_counts().items()},
            **combined.summary,
        }
        if combined.uncovered:
            ctx.issues.append(QualityIssue(
                "risk_warning_intervals", "dated_history_covers_live_names", "warning",
                "以下在市证券名称带风险警示但没有带日期的区间覆盖，仅保留当前状态: "
                + ", ".join(combined.uncovered[:30]), len(combined.uncovered)))

    def _ingest_risk_bulletins(self, ctx: RunContext) -> pd.DataFrame | None:
        """Fetch SSE/BSE risk-warning bulletins window by window (append-only)."""
        step = "risk_warning_bulletins"
        existing = read_canonical(self.root, step)
        log = read_canonical(self.root, "bulletin_fetch_log")
        today = self.clock().date().isoformat()
        parts, log_rows, unmapped = [], [], 0
        consecutive: dict[str, int] = {}
        for exchange, keyword, start, end in bulletin_windows(today, log):
            slug = RISK_KEYWORDS[keyword]
            if consecutive.get(exchange, 0) >= BULLETIN_MAX_CONSECUTIVE_FAILURES:
                # The exchange is refusing requests (anti-crawling); leave the
                # remaining windows for the next run instead of hammering it.
                ctx.count(step, "deferred_windows")
                continue
            try:
                if exchange == "SSE":
                    raw = self.provider.fetch_sse_bulletins(keyword, start, end)
                    frame = normalize_sse_bulletins(raw, keyword, ctx.run_id, SCHEMA_VERSION)
                else:
                    raw = self.provider.fetch_bse_announcements(keyword, start, end)
                    frame, dropped = normalize_bse_announcements(raw, keyword, ctx.run_id, SCHEMA_VERSION, ctx.master)
                    unmapped += dropped
            except Exception as exc:
                ctx.error(step, f"{exchange}:{slug}:{start}", exc)
                ctx.count(step, "failed_windows")
                consecutive[exchange] = consecutive.get(exchange, 0) + 1
                continue
            consecutive[exchange] = 0
            self._raw(ctx, "risk_bulletins", f"{exchange}_{slug}_{start}_{end}", raw,
                      f"exchange_bulletins.{exchange}.{slug}")
            parts.append(frame)
            log_rows.append({"exchange": exchange, "keyword": keyword, "window_start": start, "window_end": end,
                             "rows": len(frame), "run_id": ctx.run_id})
            ctx.count(step, "windows")
        failed = ctx.counters.get(step, {}).get("failed_windows", 0)
        deferred = ctx.counters.get(step, {}).get("deferred_windows", 0)
        if failed or deferred:
            ctx.issues.append(QualityIssue(step, "fetch_windows", "warning",
                                           f"交易所公告抓取：{failed} 个窗口失败、{deferred} 个窗口推迟，下次运行重试",
                                           failed + deferred))
        if unmapped:
            ctx.count(step, "bse_unmapped_announcements", unmapped)
        merged = existing
        for part in parts:
            merged = merge_bulletins(merged, part)
        if merged is not None and not merged.empty:
            write_canonical_frame(self.root, step, merged)
        if log_rows:
            new_log = pd.DataFrame(log_rows)
            if log is not None and not log.empty:
                new_log = pd.concat([log, new_log], ignore_index=True).drop_duplicates(
                    ["exchange", "keyword", "window_start", "window_end"], keep="last")
            write_canonical_frame(self.root, "bulletin_fetch_log", new_log)
        return merged

    # ----------------------------------------------------------- corporate

    def _ingest_corporate(self, ctx: RunContext) -> None:
        """Share-capital changes and dividends (append-only, window log)."""
        log = read_canonical(self.root, "corporate_fetch_log")
        today = self.clock().date()
        log_rows: list[dict[str, Any]] = []
        jobs = [
            ("share_capital", [(f"{field}_{start}_{end}", (field, start, end))
                               for field, start, end in share_capital_windows(today, log)]),
            ("dividends", [(dividend_window(report), (report,)) for report in dividend_report_dates(today, log)]),
        ]
        for dataset, windows in jobs:
            merged = read_canonical(self.root, dataset)
            consecutive = 0
            for name, args in windows:
                if consecutive >= CORPORATE_MAX_CONSECUTIVE_FAILURES:
                    ctx.count(dataset, "deferred_windows")
                    continue
                try:
                    if dataset == "share_capital":
                        raw = self.provider.fetch_share_capital(*args)
                        part = normalize_share_capital(raw, ctx.run_id, ctx.ingested_at)
                    else:
                        raw = self.provider.fetch_dividends(*args)
                        part = normalize_dividends(raw, args[0], ctx.run_id, ctx.ingested_at)
                except Exception as exc:
                    ctx.error(dataset, name, exc)
                    ctx.count(dataset, "failed_windows")
                    consecutive += 1
                    continue
                consecutive = 0
                self._raw(ctx, dataset, name, raw,
                          SOURCE_SHARE_CAPITAL if dataset == "share_capital" else SOURCE_DIVIDENDS)
                merged = (merge_share_capital if dataset == "share_capital" else merge_dividends)(merged, part)
                log_rows.append({"dataset": dataset, "window": name, "rows": len(part), "run_id": ctx.run_id})
                ctx.count(dataset, "windows")
            if merged is not None and not merged.empty:
                write_canonical_frame(self.root, dataset, merged)
            failed = ctx.counters.get(dataset, {}).get("failed_windows", 0)
            deferred = ctx.counters.get(dataset, {}).get("deferred_windows", 0)
            if failed or deferred:
                ctx.issues.append(QualityIssue(
                    dataset, "fetch_windows", "warning",
                    f"{dataset} 抓取：{failed} 个窗口失败、{deferred} 个窗口推迟，下次运行重试", failed + deferred))
        if log_rows:
            write_canonical_frame(self.root, "corporate_fetch_log", merge_fetch_log(log, log_rows))

    # ----------------------------------------------------------------- ETF

    def _ingest_etf(self, ctx: RunContext) -> None:
        """ETF lists (each fund's tracking index), daily shares outstanding,
        and daily bars of the funds in the broad-index groups.  Problems are
        warnings: this data feeds a dashboard, not decisions."""
        master = self._ingest_etf_master(ctx)
        self._ingest_etf_shares(ctx)
        self._ingest_etf_bars(ctx, master)
        self._ingest_etf_holders(ctx, master)

    def _ingest_etf_master(self, ctx: RunContext) -> pd.DataFrame | None:
        step = "etf_master"
        master = read_canonical(self.root, step)
        try:
            lists = self.provider.fetch_etf_lists()
        except Exception as exc:
            ctx.error(step, "lists", exc)
            ctx.issues.append(QualityIssue(step, "fetch", "warning", f"ETF 列表下载失败，沿用上次的列表：{exc}"))
            return master
        incoming = []
        for exchange, source, normalize in (("sse", SOURCE_ETF_LIST_SSE, normalize_etf_list_sse),
                                            ("szse", SOURCE_ETF_LIST_SZSE, normalize_etf_list_szse)):
            raw = lists.get(exchange)
            self._raw(ctx, step, f"{exchange}_list", raw, source)
            part = normalize(raw, ctx.run_id, ctx.ingested_at)
            ctx.count(step, f"{exchange}_funds", len(part))
            incoming.append(part)
        merged = merge_etf_master(master, pd.concat(incoming, ignore_index=True))
        write_canonical_frame(self.root, step, merged)
        return merged

    def _ingest_etf_bars(self, ctx: RunContext, master: pd.DataFrame | None) -> None:
        """Tencent daily bars of every fund in configs/etf (prices turn share
        changes into money).  A fund no longer listed stops refreshing."""
        assert ctx.expected_latest is not None
        step = "etf_bars"
        members = group_members(master, load_etf_groups(self.root / self.config.etf_groups_path))
        latest_list = None if master is None or master.empty else master["listed_run_id"].max()
        start = pd.to_datetime(self.config.etf_sse_start).date()
        jobs: dict[str, date] = {}
        for row in members.drop_duplicates("symbol").itertuples(index=False):
            bounds = self._partition_bounds(step, row.symbol)
            if bounds is not None and row.listed_run_id != latest_list:
                ctx.count(step, "frozen_unlisted")
                continue
            if bounds is not None and ctx.mode == "incremental":
                fetch_start = session_offset(ctx.open_dates, bounds[1], self.config.overlap_sessions)
            else:
                listed = as_date(row.list_date)
                fetch_start = max(start, listed) if listed else start
            jobs[row.symbol] = fetch_start
        ctx.count(step, "requested", len(jobs))

        def fetch(symbol: str) -> FetchResult:
            return self.provider.fetch_index_daily(etf_prefix(symbol) + symbol, self._date_str(jobs[symbol]),
                                                   self._date_str(ctx.expected_latest))

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {executor.submit(fetch, symbol): symbol for symbol in jobs}
            try:
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        self._store_etf_bars(ctx, symbol, start, future.result())
                    except Exception as exc:
                        ctx.error(step, symbol, exc)
                        ctx.count(step, "failed")
            except BaseException:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
        failed = ctx.counters.get(step, {}).get("failed", 0)
        if failed:
            ctx.issues.append(QualityIssue(step, "fetch", "warning",
                                           f"{failed}/{len(jobs)} 只 ETF 日线下载失败，下次运行重试", failed))

    def _ingest_intraday(self, ctx: RunContext) -> None:
        """1-minute bars of the configured codes (every session the source still keeps), then the
        latest session's 3-second trades of the funds among them, checked against those bars."""
        assert ctx.expected_latest is not None
        day = ctx.expected_latest
        by_day: dict[date, list[pd.DataFrame]] = {}
        for code in self.config.intraday_codes:
            source = SOURCE_BARS if code[:2] in ("sh", "sz") else SOURCE_FUTURES
            try:
                raw = self.provider.fetch_intraday_bars(code)
            except NotImplementedError:
                return
            except Exception as exc:
                ctx.error("intraday_bars", code, exc)
                ctx.count("intraday_bars", "failed")
                continue
            self._raw(ctx, "intraday_bars", code, raw, source)
            frame = normalize_intraday_bars(raw, code, day, ctx.run_id, ctx.ingested_at, source)
            ctx.count("intraday_bars", "codes")
            for session, part in frame.groupby("trade_date"):
                by_day.setdefault(session, []).append(part)
        for session, parts in sorted(by_day.items()):
            partition = f"trade_date={session}"
            merged = merge_intraday_day(read_canonical(self.root, "intraday_bars", partition), pd.concat(parts),
                                        INTRADAY_BAR_COLUMNS)
            write_canonical_frame(self.root, "intraday_bars", merged, partition=partition)
        today = pd.concat(by_day.get(day, [pd.DataFrame(columns=INTRADAY_BAR_COLUMNS)]), ignore_index=True)
        trades = []
        for code in filter(intraday_has_trades, self.config.intraday_codes):
            try:
                raw = self.provider.fetch_intraday_trades(code)
            except Exception as exc:
                ctx.error("intraday_trades", code, exc)
                ctx.count("intraday_trades", "failed")
                continue
            frame = normalize_intraday_trades(raw, code, day, ctx.run_id, ctx.ingested_at)
            problem = check_intraday_trades(frame, today[today["symbol"] == intraday_symbol(code)])
            if problem:  # kept aside, so a rebuild does not file it under this day either
                self._raw(ctx, "intraday_trades_rejected", f"{code}_{day:%Y%m%d}", raw, SOURCE_TRADES)
                ctx.issues.append(QualityIssue("intraday_trades", "session_check", "warning",
                                               f"{code} {day} 逐笔未入库：{problem}"))
                ctx.count("intraday_trades", "rejected")
                continue
            self._raw(ctx, "intraday_trades", f"{code}_{day:%Y%m%d}", raw, SOURCE_TRADES)
            trades.append(frame)
            ctx.count("intraday_trades", "stored")
        if trades:
            partition = f"trade_date={day}"
            merged = merge_intraday_day(read_canonical(self.root, "intraday_trades", partition), pd.concat(trades),
                                        INTRADAY_TRADE_COLUMNS)
            write_canonical_frame(self.root, "intraday_trades", merged, partition=partition)
        failed = sum(ctx.counters.get(step, {}).get("failed", 0) for step in ("intraday_bars", "intraday_trades"))
        if failed:
            ctx.issues.append(QualityIssue("intraday", "fetch", "warning",
                                           f"日内数据 {failed} 个请求失败（逐笔只有当天能补，分钟线一周内可补）", failed))

    def _ingest_etf_holders(self, ctx: RunContext, master: pd.DataFrame | None) -> None:
        """Top holders from the broad-index funds' annual and interim reports.

        Report lists are refreshed once a week per fund.  New reports (from
        2015) of all funds are fetched newest period first, up to
        ``etf_holder_reports_per_run`` per run, so the latest period of every
        fund comes in first and history follows on later runs.  A fund's list
        counts as refreshed only once all its reports are in.  Eastmoney cuts
        the server off after a burst of report requests, so a few failures in
        a row put the rest off to the next run instead of retrying each one.
        """
        step = "etf_holders"
        budget = self.config.etf_holder_reports_per_run
        if budget <= 0:
            return
        members = group_members(master, load_etf_groups(self.root / self.config.etf_groups_path))
        log = read_canonical(self.root, "etf_holders_fetch_log")
        holders = read_canonical(self.root, "etf_top_holders")
        log_rows: list[dict[str, Any]] = []
        failures = 0
        listed: dict[str, int] = {}
        pending: list[tuple[str, dict]] = []
        for symbol in lists_to_refresh(sorted(set(members["symbol"])), log, self.clock().date()):
            if failures >= CORPORATE_MAX_CONSECUTIVE_FAILURES:
                ctx.count(step, "deferred_lists")
                continue
            try:
                listing = self.provider.fetch_fund_reports(symbol)
            except Exception as exc:
                ctx.error(step, symbol, exc)
                ctx.count(step, "failed_lists")
                failures += 1
                continue
            failures = 0
            self._raw(ctx, "etf_report_lists", symbol, listing, SOURCE_FUND_REPORT)
            listed[symbol] = len(listing)
            pending += [(symbol, report) for report in reports_to_fetch(listing, log)]
        pending.sort(key=lambda item: (item[1]["report_date"], item[1]["notice_date"], item[0]), reverse=True)
        unfinished = {symbol for symbol, _ in pending[budget:]}
        failures = 0
        for symbol, report in pending[:budget]:
            if failures >= CORPORATE_MAX_CONSECUTIVE_FAILURES:
                ctx.count(step, "deferred_reports")
                unfinished.add(symbol)
                continue
            try:
                text = self.provider.fetch_report_text(report["art_code"])
            except Exception as exc:
                ctx.error(step, report["art_code"], exc)
                ctx.count(step, "failed_reports")
                unfinished.add(symbol)
                failures += 1
                continue
            failures = 0
            raw = pd.DataFrame([{"symbol": symbol, **{k: str(v) for k, v in report.items()}, "text": text}])
            self._raw(ctx, "etf_reports", report["art_code"], raw, SOURCE_FUND_REPORT)
            part = holder_rows(symbol, report, text, ctx.run_id, ctx.ingested_at)
            holders = merge_holders(holders, part)
            ctx.count(step, "reports")
            if part.empty:
                ctx.count(step, "reports_without_table")  # 2026 interim reports dropped the table
            log_rows.append({"dataset": "report", "window": report["art_code"], "rows": len(part),
                             "run_id": ctx.run_id})
        if len(pending) > budget:
            ctx.count(step, "deferred_reports", len(pending) - budget)
        log_rows += [{"dataset": "report_list", "window": symbol, "rows": rows, "run_id": ctx.run_id}
                     for symbol, rows in listed.items() if symbol not in unfinished]
        if holders is not None and not holders.empty:
            write_canonical_frame(self.root, "etf_top_holders", holders)
        if log_rows:
            write_canonical_frame(self.root, "etf_holders_fetch_log", merge_fetch_log(log, log_rows))
        counters = ctx.counters.get(step, {})
        failed = counters.get("failed_lists", 0) + counters.get("failed_reports", 0)
        if failed:
            ctx.issues.append(QualityIssue(step, "fetch", "warning",
                                           f"ETF 定期报告：{failed} 个请求失败，下次运行重试", failed))

    def _store_etf_bars(self, ctx: RunContext, symbol: str, start: date, result: FetchResult) -> None:
        assert ctx.expected_latest is not None
        step = "etf_bars"
        incoming = normalize_etf_bars(result.frame, symbol, start, ctx.expected_latest, ctx.run_id,
                                      ctx.ingested_at, result.source)
        existing = read_canonical(self.root, step, f"symbol={symbol}")
        if incoming.empty:
            self._raw(ctx, step, symbol, result.frame, result.source)
            ctx.count(step, "empty")
            return
        issues = validate_index_refresh(incoming, existing, symbol)
        if has_blocking(issues):
            # Kept aside so a rebuild does not replay it.
            self._raw(ctx, "etf_bars_rejected", symbol, result.frame, result.source)
            ctx.issues.extend(replace(issue, dataset=step, severity="warning") for issue in issues)
            ctx.count(step, "rejected")
            return
        self._raw(ctx, step, symbol, result.frame, result.source)
        write_canonical_frame(self.root, step, merge_index_bars(existing, incoming), partition=f"symbol={symbol}")
        ctx.count(step, "updated")

    def _ingest_etf_shares(self, ctx: RunContext) -> None:
        """Daily ETF shares outstanding: SSE one day per request (newest
        first, a capped number per run), SZSE one month per request."""
        assert ctx.expected_latest is not None
        log = read_canonical(self.root, "etf_shares_fetch_log")
        merged = read_canonical(self.root, "etf_shares")
        log_rows: list[dict[str, Any]] = []
        step = "etf_shares"
        days, left = sse_etf_dates(ctx.open_dates, pd.to_datetime(self.config.etf_sse_start).date(),
                                   ctx.expected_latest, log, self.config.etf_sse_max_dates_per_run)
        jobs: list[tuple[str, str, tuple[str, ...]]] = [
            ("sse", sse_window(day), (self._date_str(day),)) for day in days]
        jobs += [("szse", szse_window(first), (self._date_str(first), self._date_str(last)))
                 for first, last in szse_etf_months(pd.to_datetime(self.config.etf_szse_start).date(),
                                                    ctx.expected_latest, log)]
        consecutive = {"sse": 0, "szse": 0}
        for exchange, window, args in jobs:
            if consecutive[exchange] >= ETF_MAX_CONSECUTIVE_FAILURES:
                ctx.count(step, f"{exchange}_deferred")
                continue
            try:
                if exchange == "sse":
                    raw = self.provider.fetch_etf_shares_sse(*args)
                    part, counts = normalize_etf_sse(raw, pd.to_datetime(args[0]).date(), ctx.run_id,
                                                     ctx.ingested_at)
                else:
                    raw = self.provider.fetch_etf_shares_szse(*args)
                    part, counts = normalize_etf_szse(raw, ctx.run_id, ctx.ingested_at)
            except Exception as exc:
                ctx.error(step, window, exc)
                ctx.count(step, f"{exchange}_failed")
                consecutive[exchange] += 1
                continue
            consecutive[exchange] = 0
            self._raw(ctx, step, window, raw, SOURCE_ETF_SSE if exchange == "sse" else SOURCE_ETF_SZSE)
            merged = merge_etf_shares(merged, part)
            ctx.count(step, f"{exchange}_windows")
            ctx.count(step, "rows", len(part))
            for key, value in counts.items():
                if value:
                    ctx.count(step, key, value)
            if part.empty:
                ctx.count(step, f"{exchange}_empty")
            row = etf_fetch_log_row(window, len(part), ctx.run_id)
            if row is not None:
                log_rows.append(row)
        if merged is not None and not merged.empty:
            write_canonical_frame(self.root, step, merged)
        if log_rows:
            write_canonical_frame(self.root, "etf_shares_fetch_log", merge_fetch_log(log, log_rows))
        counters = ctx.counters.get(step, {})
        failed = counters.get("sse_failed", 0) + counters.get("szse_failed", 0)
        deferred = counters.get("sse_deferred", 0) + counters.get("szse_deferred", 0)
        if failed or deferred:
            ctx.issues.append(QualityIssue(
                step, "fetch_windows", "warning",
                f"ETF 份额抓取：{failed} 个请求失败、{deferred} 个推迟，下次运行重试", failed + deferred))
        if left:
            ctx.count(step, "sse_backlog", left)

    # -------------------------------------------------------- fundamentals

    def _ingest_fundamentals(self, ctx: RunContext) -> None:
        """Financial statements, versioned.  Windows are processed in name
        order (as the rebuild replays them) so version numbers match."""
        log = read_canonical(self.root, "fundamentals_fetch_log")
        today = self.clock().date()
        log_rows: list[dict[str, Any]] = []
        for statement in STATEMENTS:
            dataset = f"fin_{statement}"
            own_log = None if log is None else log[log["dataset"] == dataset]
            merged = read_canonical(self.root, dataset)
            consecutive = 0
            for name, ctype, field, start, end in sorted(financial_windows(today, own_log)):
                if consecutive >= CORPORATE_MAX_CONSECUTIVE_FAILURES:
                    ctx.count(dataset, "deferred_windows")
                    continue
                try:
                    raw = self.provider.fetch_financial_statement(statement, ctype, field, start, end)
                    part, counts = normalize_financials(raw, statement, ctype, ctx.run_id, ctx.ingested_at)
                except Exception as exc:
                    ctx.error(dataset, name, exc)
                    ctx.count(dataset, "failed_windows")
                    consecutive += 1
                    continue
                consecutive = 0
                self._raw(ctx, "financials", f"{statement}_{name}", raw, f"{SOURCE_FINANCIALS}.{ctype}")
                merged, added = merge_financial_versions(merged, part, statement)
                ctx.count(dataset, "windows")
                ctx.count(dataset, "new_versions", added)
                for key in ("not_quarter_end", "no_notice_date"):
                    if counts[key]:
                        ctx.count(dataset, f"dropped_{key}", counts[key])
                log_rows.append({"dataset": dataset, "window": name, "rows": len(part), "run_id": ctx.run_id})
            if merged is not None and not merged.empty:
                write_canonical_frame(self.root, dataset, merged)
            failed = ctx.counters.get(dataset, {}).get("failed_windows", 0)
            deferred = ctx.counters.get(dataset, {}).get("deferred_windows", 0)
            if failed or deferred:
                ctx.issues.append(QualityIssue(
                    dataset, "fetch_windows", "warning",
                    f"{dataset} 抓取：{failed} 个窗口失败、{deferred} 个窗口推迟，下次运行重试", failed + deferred))
        log_rows += self._ingest_forecasts(ctx, log, today)
        if log_rows:
            write_canonical_frame(self.root, "fundamentals_fetch_log", merge_fetch_log(log, log_rows))

    def _ingest_forecasts(self, ctx: RunContext, log: pd.DataFrame | None, today: date) -> list[dict[str, Any]]:
        """Earnings forecasts (业绩预告, forecasts.py), logged with the statements' windows."""
        step = FORECAST_DATASET
        own_log = None if log is None else log[log["dataset"] == step]
        merged = read_canonical(self.root, step)
        log_rows: list[dict[str, Any]] = []
        consecutive = 0
        for name, field, start, end in sorted(forecast_windows(today, own_log)):
            if consecutive >= CORPORATE_MAX_CONSECUTIVE_FAILURES:
                ctx.count(step, "deferred_windows")
                continue
            try:
                raw = self.provider.fetch_earnings_forecast(field, start, end)
            except NotImplementedError:
                return log_rows
            except Exception as exc:
                ctx.error(step, name, exc)
                ctx.count(step, "failed_windows")
                consecutive += 1
                continue
            consecutive = 0
            self._raw(ctx, step, name, raw, SOURCE_FORECASTS)
            part = normalize_forecasts(raw, ctx.run_id, ctx.ingested_at)
            merged = merge_forecasts(merged, part)
            ctx.count(step, "windows")
            log_rows.append({"dataset": step, "window": name, "rows": len(part), "run_id": ctx.run_id})
        if merged is not None and not merged.empty:
            write_canonical_frame(self.root, step, merged)
            ctx.summaries[step] = {"rows": int(len(merged))}
        failed = ctx.counters.get(step, {}).get("failed_windows", 0)
        deferred = ctx.counters.get(step, {}).get("deferred_windows", 0)
        if failed or deferred:
            ctx.issues.append(QualityIssue(step, "fetch_windows", "warning",
                                           f"业绩预告抓取：{failed} 个窗口失败、{deferred} 个窗口推迟，下次运行重试",
                                           failed + deferred))
        return log_rows

    # ------------------------------------------------------ classification

    def _ingest_classification(self, ctx: RunContext) -> None:
        self._ingest_sw_classification(ctx)
        self._ingest_index_weights(ctx)

    def _ingest_sw_classification(self, ctx: RunContext) -> None:
        """Shenwan history.  A raw copy is kept only when the workbooks
        changed, and the intervals always carry the run id of the raw copy
        they were built from, so a rebuild reproduces them exactly."""
        step = "industry_sw"
        try:
            files = self.provider.fetch_sw_classification()
        except Exception as exc:
            ctx.error(step, "download", exc)
            ctx.issues.append(QualityIssue(step, "ingestion_success", "warning", f"申万行业分类下载失败: {exc}"))
            return
        latest = self._latest_raw_run(step, ("history", "codes"))
        changed = latest is None or any(
            not same_frame(frame, pd.read_parquet(latest[1] / f"{name}.parquet")) for name, frame in files.items())
        if changed:
            for name, frame in files.items():
                self._raw(ctx, step, name, frame, SOURCE_SW)
            raw_run = ctx.run_id
        else:
            raw_run = latest[0]
            ctx.count(step, "unchanged")
        mapping = load_sw2014_mapping(self.root / self.config.sw_mapping_path)
        intervals, missing = normalize_sw_classification(files["history"], files["codes"], mapping, raw_run,
                                                         run_id_to_iso(raw_run))
        if intervals.empty:
            ctx.issues.append(QualityIssue(step, "not_empty", "blocking", "申万行业分类为空，保留已有数据"))
            return
        existing = read_canonical(self.root, step)
        if existing is not None and len(intervals) < len(existing) * 0.99:
            ctx.issues.append(QualityIssue(
                step, "history_not_shrunk", "blocking",
                f"申万行业区间从 {len(existing)} 行缩减到 {len(intervals)} 行，保留已有数据"))
            return
        if missing:
            ctx.issues.append(QualityIssue(
                step, "sw2014_mapping_complete", "warning",
                "以下申万旧版代码不在映射表中（记为未分类）: " + ", ".join(missing[:30]), len(missing)))
        write_canonical_frame(self.root, step, intervals)
        ctx.count(step, "intervals", len(intervals))

    def _ingest_index_weights(self, ctx: RunContext) -> None:
        """CSIndex weights: one snapshot per (index, publication date)."""
        step = "index_weights"
        merged = read_canonical(self.root, step)
        seen = set() if merged is None else set(zip(merged["index_code"], merged["as_of_date"]))
        for index_code in self.config.index_weight_symbols:
            try:
                raw = self.provider.fetch_index_weights(index_code)
                part = normalize_index_weights(raw, index_code, ctx.run_id, ctx.ingested_at)
            except Exception as exc:
                ctx.error(step, index_code, exc)
                ctx.issues.append(QualityIssue(step, "ingestion_success", "warning",
                                               f"{index_code} 成分权重下载失败: {exc}"))
                continue
            if part.empty or (index_code, part["as_of_date"].iloc[0]) in seen:
                ctx.count(step, "unchanged")
                continue
            self._raw(ctx, step, index_code, raw, SOURCE_INDEX_WEIGHTS)
            merged = merge_index_weights(merged, part)
            ctx.count(step, "snapshots")
        if merged is not None and not merged.empty:
            write_canonical_frame(self.root, step, merged)

    def _latest_raw_run(self, dataset: str, names: tuple[str, ...]) -> tuple[str, Path] | None:
        base = self.root / "data" / "raw" / self.config.provider / dataset
        runs = sorted(base.glob("run_id=*")) if base.exists() else []
        for directory in reversed(runs):
            if all((directory / f"{name}.parquet").exists() for name in names):
                return directory.name.removeprefix("run_id="), directory
        return None

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

    def _audit(self, ctx: RunContext, universe: list[UniverseItem]) -> None:
        assert ctx.expected_latest is not None
        scope = {item.symbol for item in universe} if self.config.daily_universe == "configured" else None
        result = run_audit(self.root, ctx.expected_latest, ctx.start_date,
                           self.config.min_latest_coverage, ctx.run_id, universe=scope)
        ctx.issues.extend(result.issues)
        ctx.summaries["audit"] = result.summary
        if not result.gaps.empty:
            write_canonical_frame(self.root, "bar_gaps", result.gaps)
        else:
            canonical_path(self.root, "bar_gaps").unlink(missing_ok=True)

    # ------------------------------------------------------------ finalize

    def _finalize(self, ctx: RunContext, manifest_path: Path, fatal: BaseException | None) -> dict[str, Any]:
        summary = quality_summary(ctx.issues)
        daily = ctx.counters.get("daily_bars", {})
        usable = daily.get("updated", 0) + daily.get("frozen_delisted", 0)
        if fatal is not None or ("daily_bars" in self.steps and usable == 0):
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

