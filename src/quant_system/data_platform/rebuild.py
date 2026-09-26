"""Rebuild the canonical layer from the immutable raw layer.

Every canonical row is derived from a raw vendor response, so the canonical
store can be regenerated with the current normalization code.  This is how
normalization fixes (units, factor conventions) reach existing history, and
how a rebuild is verified: build into a staging directory, diff against the
live store, then optionally swap it in (the old store is archived, not
deleted).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from .audit import run_audit
from .config import DataPlatformConfig
from .financials import STATEMENTS, merge_financial_versions, normalize_financials
from .corporate import (
    load_sw2014_mapping,
    merge_dividends,
    merge_fetch_log,
    merge_index_weights,
    merge_share_capital,
    normalize_dividends,
    normalize_index_weights,
    normalize_share_capital,
    normalize_sw_classification,
)
from .normalization import (
    SOURCE_EASTMONEY_DAILY,
    SOURCE_EASTMONEY_INDEX,
    SOURCE_SINA_DAILY,
    SOURCE_SINA_RAW_DAILY,
    SOURCE_TENCENT_DAILY,
    SOURCE_TENCENT_INDEX,
    SCHEMA_VERSION,
    as_date,
    calendar_open_dates,
    clip_bars,
    drop_invalid_price_rows,
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
    recompute_bar_derived_fields,
)
from .quality import QualityIssue, has_blocking, issues_frame, validate_adjustment_factors, validate_bars, validate_volume_units
from .risk_history import (
    RISK_KEYWORDS,
    derive_risk_intervals,
    merge_bulletins,
    normalize_bse_announcements,
    normalize_sse_bulletins,
)
from .sessions import latest_final_session, parse_hhmm
from .storage import (
    CatalogError,
    build_duckdb_catalog,
    canonical_inventory,
    canonical_path,
    read_canonical,
    read_raw_source,
    write_canonical_frame,
    write_parquet_atomic,
)
from .utils import code_version, json_dump, run_id_to_iso, unique_run_id, utc_now_iso

LOGGER = logging.getLogger("rebuild")
RUN_DIR = re.compile(r"^run_id=(\d{8}T\d{6}Z)$")


def _raw_runs(raw_root: Path, dataset: str) -> list[tuple[str, Path]]:
    directory = raw_root / dataset
    if not directory.exists():
        return []
    runs = []
    for child in directory.iterdir():
        match = RUN_DIR.match(child.name)
        if match and child.is_dir():
            runs.append((match.group(1), child))
    return sorted(runs)


def _raw_files_by_name(raw_root: Path, dataset: str) -> dict[str, list[tuple[str, Path]]]:
    grouped: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for run_id, directory in _raw_runs(raw_root, dataset):
        for path in directory.glob("*.parquet"):
            grouped[path.stem].append((run_id, path))
    return grouped


def infer_daily_source(frame: pd.DataFrame) -> str:
    """Identify legacy raw files written before sources were recorded."""
    if "日期" in frame.columns:
        return SOURCE_EASTMONEY_DAILY
    if "outstanding_share" in frame.columns:
        return SOURCE_SINA_DAILY
    if "prevclose" in frame.columns:
        return SOURCE_SINA_RAW_DAILY
    return SOURCE_TENCENT_DAILY


def _run_datetime(run_id: str) -> datetime:
    return datetime.fromisoformat(run_id_to_iso(run_id))


class CanonicalRebuilder:
    def __init__(self, root: Path, config: DataPlatformConfig, workers: int = 8) -> None:
        self.root = root.resolve()
        self.config = config
        self.workers = workers
        self.raw_root = self.root / "data" / "raw" / config.provider
        self.rebuild_id = unique_run_id(self.root)
        self.staging = self.root / "data" / "staging" / f"rebuild_{self.rebuild_id}"
        self.start_date = pd.to_datetime(config.start_date).date()
        self.final_time = parse_hhmm(config.session_final_time)
        self.issues: list[QualityIssue] = []
        self.counters: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ api

    def run(self, apply: bool = False) -> dict[str, Any]:
        LOGGER.info("Rebuilding canonical layer into %s", self.staging)
        started_at = utc_now_iso()
        open_dates, calendar_end = self._rebuild_calendar()
        self.open_dates, self.calendar_end = open_dates, calendar_end
        master = self._rebuild_security_master()
        self._rebuild_daily_bars(master, calendar_end)
        self._rebuild_factors(open_dates)
        self._rebuild_index_bars(calendar_end)
        self._rebuild_snapshots()
        self._rebuild_status_history(master)
        self._rebuild_corporate()
        self._rebuild_classification()
        self._rebuild_fundamentals()
        audit = run_audit(self.staging, calendar_end, self.start_date,
                          self.config.min_latest_coverage, f"rebuild_{self.rebuild_id}")
        if not audit.gaps.empty:
            write_canonical_frame(self.staging, "bar_gaps", audit.gaps)
        diff = self._diff()
        blocking = has_blocking(self.issues) or has_blocking(audit.issues)
        report = {
            "run_id": self.rebuild_id,
            "mode": "rebuild",
            "status": "partial" if blocking else "complete",
            "provider": self.config.provider,
            "config_hash": self.config.config_hash,
            "code_version": code_version(self.root),
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "staging": self.staging.relative_to(self.root).as_posix(),
            "expected_latest_date": str(calendar_end),
            "counters": dict(self.counters),
            "quality_summary": {
                "blocking": sum(issue.severity == "blocking" for issue in self.issues),
                "warning": sum(issue.severity == "warning" for issue in self.issues),
            },
            "audit": audit.summary,
            "audit_issues": [issue.to_dict() for issue in audit.issues],
            "diff": diff,
            "applied": False,
        }
        write_parquet_atomic(issues_frame(self.issues), self.staging / "rebuild_issues.parquet")
        if apply:
            if blocking:
                raise RuntimeError("Rebuild produced blocking issues; staging kept for inspection")
            report["archived_to"] = self._swap()
            report["applied"] = True
            report["data_version"] = canonical_inventory(self.root)[1]
            manifest_path = self.root / "data" / "manifests" / f"{self.rebuild_id}.json"
            # Written before the catalog so ingestion_runs lists this rebuild.
            json_dump(manifest_path, report)
            try:
                report["database_path"] = str(build_duckdb_catalog(self.root))
                report["catalog_status"] = "built"
            except CatalogError as exc:
                LOGGER.error("%s", exc)
                report["catalog_status"] = f"failed: {exc}"
                report["status"] = "partial"
            json_dump(manifest_path, report)
        json_dump(self.staging.parent / f"rebuild_{self.rebuild_id}.json", report)
        return report

    # ------------------------------------------------------------- datasets

    def _rebuild_calendar(self) -> tuple[list[date], date]:
        runs = _raw_runs(self.raw_root, "trading_calendar")
        if not runs:
            raise RuntimeError("No raw trading calendar to rebuild from")
        run_id, directory = runs[-1]
        raw = pd.read_parquet(next(directory.glob("*.parquet")))
        open_dates = calendar_open_dates(raw)
        current = read_canonical(self.root, "trading_calendar")
        if current is not None and not current.empty:
            end = max(current["trade_date"])
        else:
            end = latest_final_session(open_dates, _run_datetime(run_id), self.final_time)
        calendar = normalize_calendar(raw, self.start_date, end, run_id, run_id_to_iso(run_id))
        write_canonical_frame(self.staging, "trading_calendar", calendar)
        return open_dates, end

    def _rebuild_security_master(self) -> pd.DataFrame | None:
        runs = _raw_runs(self.raw_root, "security_lists")
        current = read_canonical(self.root, "security_master")
        if not runs:
            # Built before exchange lists were ingested: keep what exists.
            for dataset in ("security_master", "instruments"):
                source = canonical_path(self.root, dataset)
                if source.exists():
                    target = canonical_path(self.staging, dataset)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            return current
        run_id, directory = runs[-1]
        lists = {path.stem: pd.read_parquet(path) for path in directory.glob("*.parquet")}
        manual = [asdict(item) for item in self.config.manual_delistings]
        master = normalize_security_master(lists, manual, run_id, run_id_to_iso(run_id))
        if current is not None:
            inferred = current[
                (current["source"] == "inferred.missing_from_listing")
                & ~current["symbol"].isin(master["symbol"])
            ]
            master = pd.concat([master, inferred], ignore_index=True)
        write_canonical_frame(self.staging, "security_master", master)
        return master

    def _rebuild_daily_bars(self, master: pd.DataFrame | None, calendar_end: date) -> None:
        files = _raw_files_by_name(self.raw_root, "daily_bars")
        bounds: dict[str, tuple[date | None, date | None]] = {}
        if master is not None:
            for row in master.itertuples(index=False):
                bounds[row.symbol] = (as_date(row.list_date), as_date(row.delist_date))

        def rebuild(symbol: str) -> tuple[list[QualityIssue], dict[str, int]]:
            parts = []
            for run_id, path in sorted(files[symbol]):
                raw = pd.read_parquet(path)
                source = read_raw_source(path) or infer_daily_source(raw)
                parts.append(normalize_daily_bars(raw, symbol, run_id, run_id_to_iso(run_id), source))
            parts = [part for part in parts if not part.empty]
            if not parts:
                return [QualityIssue("daily_bars", "no_data", "warning", f"{symbol} 原始层没有行情",
                                     symbol=symbol)], {}
            frame = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol", "trade_date"], keep="last")
            frame, dropped = drop_invalid_price_rows(frame)
            list_date, delist_date = bounds.get(symbol, (None, None))
            start = max(self.start_date, list_date) if list_date else self.start_date
            end = min(calendar_end, delist_date) if delist_date else calendar_end
            frame, clipped = clip_bars(frame, start, end)
            frame = recompute_bar_derived_fields(frame)
            counts = {"daily_rows_dropped_invalid": dropped, "daily_rows_clipped": clipped}
            if frame.empty:
                return [QualityIssue("daily_bars", "no_data", "warning", f"{symbol} 裁剪后没有行情",
                                     symbol=symbol)], counts
            issues = validate_bars(frame, "daily_bars", symbol) + validate_volume_units(frame, symbol)
            if not has_blocking(issues):
                write_canonical_frame(self.staging, "daily_bars", frame, partition=f"symbol={symbol}")
                counts["daily_partitions"] = 1
            return issues, counts

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            for issues, counts in executor.map(rebuild, sorted(files)):
                self.issues.extend(issues)
                for key, value in counts.items():
                    self.counters[key] += value

    def _rebuild_factors(self, open_dates: list[date]) -> None:
        files = _raw_files_by_name(self.raw_root, "adjustment_factors")
        bars_first = self._first_bar_dates()

        def rebuild(symbol: str) -> tuple[list[QualityIssue], dict[str, int]]:
            skipped = 0
            for run_id, path in sorted(files[symbol], reverse=True):
                raw = pd.read_parquet(path)
                source = read_raw_source(path)
                if source is None:
                    # Legacy raw: only Sina event files carry the 1900 baseline;
                    # the old Tencent-derived daily ratios are not usable.
                    dates = pd.to_datetime(raw["date"], errors="coerce") if "date" in raw.columns else None
                    if (
                        dates is None
                        or "hfq_factor" not in raw.columns
                        or not (dates == pd.Timestamp("1900-01-01")).any()
                    ):
                        skipped += 1
                        continue
                    source = "akshare.stock_zh_a_daily.sina"
                as_of = latest_final_session(open_dates, _run_datetime(run_id), self.final_time)
                frame = normalize_adjustment_factors(raw, symbol, run_id, run_id_to_iso(run_id), source, as_of)
                issues = validate_adjustment_factors(frame, symbol, None, bars_first.get(symbol))
                counts = {"factor_raw_skipped_legacy": skipped}
                if not has_blocking(issues):
                    write_canonical_frame(self.staging, "adjustment_factors", frame,
                                          partition=f"symbol={symbol}")
                    counts["factor_partitions"] = 1
                return issues, counts
            return [QualityIssue("adjustment_factors", "no_usable_raw", "warning",
                                 f"{symbol} 没有可用的原始复权因子", symbol=symbol)], {
                                     "factor_raw_skipped_legacy": skipped}

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            for issues, counts in executor.map(rebuild, sorted(files)):
                self.issues.extend(issues)
                for key, value in counts.items():
                    self.counters[key] += value

    def _first_bar_dates(self) -> dict[str, date]:
        glob = (self.staging / "data" / "canonical" / "daily_bars" / "**" / "*.parquet").as_posix()
        with duckdb.connect() as con:
            rows = con.execute(
                f"SELECT symbol, min(trade_date) FROM read_parquet('{glob}', hive_partitioning=false) GROUP BY 1"
            ).fetchall()
        return dict(rows)

    def _rebuild_index_bars(self, calendar_end: date) -> None:
        index_start = pd.to_datetime(self.config.index_start_date).date()
        names = {item.symbol: item.name for item in self.config.indices}
        for symbol, entries in _raw_files_by_name(self.raw_root, "index_bars").items():
            merged = None
            for run_id, path in sorted(entries):
                raw = pd.read_parquet(path)
                source = read_raw_source(path) or (
                    SOURCE_TENCENT_INDEX if "turnover" in raw.columns else SOURCE_EASTMONEY_INDEX
                )
                part = normalize_index_bars(raw, symbol, names.get(symbol, symbol), index_start, calendar_end,
                                            run_id, run_id_to_iso(run_id), source)
                if "csindex" in source:  # same session filter as ingestion
                    part = part[part["trade_date"].isin(set(self.open_dates))].reset_index(drop=True)
                merged = merge_index_bars(merged, part)
            if merged is not None and not merged.empty:
                write_canonical_frame(self.staging, "index_bars", merged, partition=f"symbol={symbol}")
                self.counters["index_partitions"] += 1

    def _rebuild_snapshots(self) -> None:
        rebuilt: set[str] = set()
        for run_id, directory in _raw_runs(self.raw_root, "market_snapshot"):
            for path in directory.glob("all_a_shares_*.parquet"):
                session = path.stem.removeprefix("all_a_shares_")
                if session == "intraday":
                    continue
                raw = pd.read_parquet(path)
                frame = normalize_market_snapshot(raw, session, run_id, run_id_to_iso(run_id),
                                                  source=read_raw_source(path) or "unknown")
                write_canonical_frame(self.staging, "market_snapshot", frame, partition=f"snapshot_date={session}")
                rebuilt.add(session)
        # Legacy raw snapshots carry no session label; keep their partitions.
        current = self.root / "data" / "canonical" / "market_snapshot"
        if current.exists():
            for partition in current.iterdir():
                session = partition.name.removeprefix("snapshot_date=")
                if session not in rebuilt:
                    target = self.staging / "data" / "canonical" / "market_snapshot" / partition.name
                    shutil.copytree(partition, target, dirs_exist_ok=True)
                    self.counters["snapshot_partitions_copied"] += 1

    def _rebuild_status_history(self, master: pd.DataFrame | None) -> None:
        events = None
        log_rows = []
        for dataset, normalizer in (("suspensions_tfp", normalize_tfp_suspensions),
                                    ("suspensions_baidu", normalize_baidu_suspensions)):
            for run_id, directory in _raw_runs(self.raw_root, dataset):
                for path in sorted(directory.glob("*.parquet")):
                    observed = pd.to_datetime(path.stem).date()
                    raw = pd.read_parquet(path)
                    if len(raw.columns) == 0:
                        continue  # empty vendor response: not a completed day (see pipeline)
                    part = normalizer(raw, observed, run_id, run_id_to_iso(run_id))
                    events = merge_suspension_events(events, part)
                    if dataset == "suspensions_baidu":
                        log_rows.append({"source": "baidu", "query_date": observed, "rows": len(part),
                                         "run_id": run_id})
        if events is not None and master is not None:
            events = events[events["symbol"].isin(master["symbol"])]
        if events is not None and not events.empty:
            write_canonical_frame(self.staging, "suspension_events", events)
        if log_rows:
            log = pd.DataFrame(log_rows).drop_duplicates(["source", "query_date"], keep="last")
            write_canonical_frame(self.staging, "suspension_fetch_log", log)
        runs = _raw_runs(self.raw_root, "security_name_changes")
        changes = None
        if runs:
            run_id, directory = runs[-1]
            changes = normalize_sz_name_changes(pd.read_parquet(directory / "szse.parquet"), run_id,
                                                run_id_to_iso(run_id))
            if not changes.empty:
                write_canonical_frame(self.staging, "security_name_changes", changes)
        bulletins, bulletin_runs = self._rebuild_risk_bulletins(master)
        if master is not None and "status" in master.columns and (runs or bulletin_runs):
            run_id = max([run for run, _ in runs] + bulletin_runs)
            sessions = [day.isoformat() for day in self.open_dates]
            combined = derive_risk_intervals(self.staging, sessions, master, changes, bulletins, run_id,
                                             SCHEMA_VERSION, str(self.calendar_end))
            if not combined.intervals.empty:
                write_canonical_frame(self.staging, "risk_warning_intervals", combined.intervals)
            if not combined.adjustments.empty:
                write_canonical_frame(self.staging, "risk_warning_adjustments", combined.adjustments)

    def _rebuild_risk_bulletins(self, master: pd.DataFrame | None) -> tuple[pd.DataFrame | None, list[str]]:
        """Replay raw exchange-bulletin windows (named EXCHANGE_slug_start_end)."""
        keywords = {slug: keyword for keyword, slug in RISK_KEYWORDS.items()}
        bulletins, log_rows, run_ids = None, [], []
        for run_id, directory in _raw_runs(self.raw_root, "risk_bulletins"):
            run_ids.append(run_id)
            for path in sorted(directory.glob("*.parquet")):
                parts = path.stem.split("_")
                exchange, start, end, slug = parts[0], parts[-2], parts[-1], "_".join(parts[1:-2])
                keyword = keywords[slug]
                raw = pd.read_parquet(path)
                if exchange == "SSE":
                    frame = normalize_sse_bulletins(raw, keyword, run_id, SCHEMA_VERSION)
                elif master is not None:
                    frame, _ = normalize_bse_announcements(raw, keyword, run_id, SCHEMA_VERSION, master)
                else:
                    continue
                bulletins = merge_bulletins(bulletins, frame)
                log_rows.append({"exchange": exchange, "keyword": keyword, "window_start": start,
                                 "window_end": end, "rows": len(frame), "run_id": run_id})
        if bulletins is not None and not bulletins.empty:
            write_canonical_frame(self.staging, "risk_warning_bulletins", bulletins)
        if log_rows:
            log = pd.DataFrame(log_rows).drop_duplicates(["exchange", "keyword", "window_start", "window_end"],
                                                         keep="last")
            write_canonical_frame(self.staging, "bulletin_fetch_log", log)
        return bulletins, run_ids

    def _rebuild_corporate(self) -> None:
        """Replay share-capital and dividend windows in run order (the same
        append-only merges as ingestion), and rebuild their fetch log."""
        log_rows: list[dict[str, Any]] = []
        for dataset in ("share_capital", "dividends"):
            merged = None
            for run_id, directory in _raw_runs(self.raw_root, dataset):
                for path in sorted(directory.glob("*.parquet")):
                    raw = pd.read_parquet(path)
                    if dataset == "share_capital":
                        part = normalize_share_capital(raw, run_id, run_id_to_iso(run_id))
                        merged = merge_share_capital(merged, part)
                    else:
                        report = path.stem.removeprefix("report_")
                        part = normalize_dividends(raw, report, run_id, run_id_to_iso(run_id))
                        merged = merge_dividends(merged, part)
                    log_rows.append({"dataset": dataset, "window": path.stem, "rows": len(part), "run_id": run_id})
            if merged is not None and not merged.empty:
                write_canonical_frame(self.staging, dataset, merged)
        if log_rows:
            write_canonical_frame(self.staging, "corporate_fetch_log", merge_fetch_log(None, log_rows))

    def _rebuild_fundamentals(self) -> None:
        """Replay statement windows run by run, in name order, through the
        same version merge as ingestion."""
        merged: dict[str, Any] = {statement: None for statement in STATEMENTS}
        log_rows: list[dict[str, Any]] = []
        for run_id, directory in _raw_runs(self.raw_root, "financials"):
            for path in sorted(directory.glob("*.parquet")):
                statement, _, name = path.stem.partition("_")
                ctype = name.split("_", 1)[0]
                part, _ = normalize_financials(pd.read_parquet(path), statement, ctype, run_id,
                                               run_id_to_iso(run_id))
                merged[statement], _ = merge_financial_versions(merged[statement], part, statement)
                log_rows.append({"dataset": f"fin_{statement}", "window": name, "rows": len(part),
                                 "run_id": run_id})
        for statement, frame in merged.items():
            if frame is not None and not frame.empty:
                write_canonical_frame(self.staging, f"fin_{statement}", frame)
        if log_rows:
            write_canonical_frame(self.staging, "fundamentals_fetch_log", merge_fetch_log(None, log_rows))

    def _rebuild_classification(self) -> None:
        runs = [(run_id, directory) for run_id, directory in _raw_runs(self.raw_root, "industry_sw")
                if (directory / "history.parquet").exists() and (directory / "codes.parquet").exists()]
        if runs:
            run_id, directory = runs[-1]
            mapping = load_sw2014_mapping(self.root / self.config.sw_mapping_path)
            intervals, _ = normalize_sw_classification(
                pd.read_parquet(directory / "history.parquet"), pd.read_parquet(directory / "codes.parquet"),
                mapping, run_id, run_id_to_iso(run_id))
            if not intervals.empty:
                write_canonical_frame(self.staging, "industry_sw", intervals)
        merged = None
        for run_id, directory in _raw_runs(self.raw_root, "index_weights"):
            for path in sorted(directory.glob("*.parquet")):
                part = normalize_index_weights(pd.read_parquet(path), path.stem, run_id, run_id_to_iso(run_id))
                merged = merge_index_weights(merged, part)
        if merged is not None and not merged.empty:
            write_canonical_frame(self.staging, "index_weights", merged)

    # ---------------------------------------------------------------- diff

    def _diff(self) -> dict[str, Any]:
        live = self.root / "data" / "canonical"
        staged = self.staging / "data" / "canonical"
        result: dict[str, Any] = {}
        datasets = sorted({p.name for p in live.iterdir() if p.is_dir()} |
                          {p.name for p in staged.iterdir() if p.is_dir()})
        with duckdb.connect() as con:
            for dataset in datasets:
                counts = {}
                for label, base in (("live", live), ("rebuilt", staged)):
                    directory = base / dataset
                    files = list(directory.rglob("*.parquet")) if directory.exists() else []
                    rows = con.execute(
                        f"SELECT COUNT(*) FROM read_parquet('{(directory / '**' / '*.parquet').as_posix()}', "
                        "union_by_name=true, hive_partitioning=false)"
                    ).fetchone()[0] if files else 0
                    counts[label] = {"files": len(files), "rows": int(rows)}
                result[dataset] = counts
            for dataset, key, spec in (
                ("daily_bars", "trade_date", DAILY_DIFF),
                ("index_bars", "trade_date", INDEX_DIFF),
                ("adjustment_factors", "effective_date", FACTOR_DIFF),
            ):
                if (live / dataset).exists() and (staged / dataset).exists():
                    result[f"{dataset}_detail"] = self._value_diff(
                        con, live / dataset, staged / dataset, key, spec
                    )
        return result

    @staticmethod
    def _value_diff(
        con: duckdb.DuckDBPyConnection, live: Path, staged: Path, key: str, spec: dict[str, str]
    ) -> dict[str, int]:
        """Row-level diff on (symbol, key); ``spec`` maps label -> column SQL."""
        def relation(directory: Path) -> str:
            glob = (directory / "**" / "*.parquet").as_posix()
            return f"read_parquet('{glob}', union_by_name=true, hive_partitioning=false)"

        columns = {}
        for label, directory in (("a", live), ("b", staged)):
            columns[label] = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {relation(directory)}").fetchall()}

        def expression(side: str, sql: str) -> str:
            # Columns renamed across schema versions: first existing wins.
            names = [name.strip() for name in sql.split("|")]
            present = [f"{side}.{name}" for name in names if name in columns[side]]
            return f"COALESCE({', '.join(present)})" if present else "NULL"

        changed = ",\n".join(
            f"count(*) FILTER (WHERE a.symbol IS NOT NULL AND b.symbol IS NOT NULL AND "
            f"{expression('a', sql)} IS DISTINCT FROM {expression('b', sql)}) AS {label}_changed"
            for label, sql in spec.items()
        )
        any_changed = " OR ".join(
            f"{expression('a', sql)} IS DISTINCT FROM {expression('b', sql)}" for sql in spec.values()
        )
        row = con.execute(
            f"""
            SELECT
                count(*) FILTER (WHERE b.symbol IS NULL) AS only_live,
                count(*) FILTER (WHERE a.symbol IS NULL) AS only_rebuilt,
                {changed},
                count(DISTINCT a.symbol) FILTER (WHERE b.symbol IS NOT NULL AND ({any_changed})) AS symbols_changed
            FROM {relation(live)} AS a
            FULL OUTER JOIN {relation(staged)} AS b ON a.symbol = b.symbol AND a.{key} = b.{key}
            """
        ).fetchdf().iloc[0].to_dict()
        return {name: int(value) for name, value in row.items()}

    def _swap(self) -> str:
        """Archive the live store and move the rebuilt one in, or change nothing.

        Plain renames fail atomically when a file is held open (Windows);
        shutil.move would fall back to copy+delete and could leave the live
        store half deleted.
        """
        live = self.root / "data" / "canonical"
        archive = self.root / "data" / "archive" / f"canonical_before_rebuild_{self.rebuild_id}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.rename(live, archive)
        try:
            os.rename(self.staging / "data" / "canonical", live)
        except OSError:
            os.rename(archive, live)
            raise
        return archive.relative_to(self.root).as_posix()


DAILY_DIFF = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "volume": "volume_shares", "turnover": "turnover_cny",
}
INDEX_DIFF = {"close": "close", "volume": "volume_shares | volume", "turnover": "turnover_cny"}
FACTOR_DIFF = {"hfq": "hfq_factor"}


def rebuild_canonical(root: Path, config: DataPlatformConfig, apply: bool = False) -> dict[str, Any]:
    return CanonicalRebuilder(root, config).run(apply=apply)
