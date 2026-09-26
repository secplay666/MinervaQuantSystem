"""Build and run a backtest from a config file, and write its artifacts."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from ..analytics.benchmark import benchmarks_for
from ..analytics.metrics import performance, relative, to_series, trading_stats
from ..analytics.report import LIMITATIONS, render_report
from ..data_platform.storage import write_parquet_atomic
from ..data_platform.utils import code_version, create_artifact_dir, file_sha256, json_dump
from ..domain.calendar import TradingCalendar
from ..domain.rules import MarketRules
from ..strategy.base import build_schedule
from ..strategy.registry import StrategyContext, build_strategy
from .config import BacktestConfig
from .engine import BacktestEngine, BacktestResult
from .market_data import MarketData, load_market_data

LOGGER = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parents[3]


def run_backtest(
    config: BacktestConfig,
    data: MarketData | None = None,
    context: StrategyContext | None = None,
) -> tuple[BacktestResult, dict[str, Any], dict[str, pd.Series]]:
    """Run one backtest.  Pass ``data`` / ``context`` to reuse loaded data
    (parameter sweeps); ``context.data`` must then be ``data``."""
    timings: dict[str, float] = {}
    started = time.perf_counter()
    if data is None:
        data = context.data if context is not None else load_market_data(config.data_source, config.data_path)
    timings["load_seconds"] = round(time.perf_counter() - started, 2)
    if context is None:
        from ..research.data import load_research_data

        context = StrategyContext(ROOT, data, lambda: load_research_data(config.data_source, config.data_path,
                                                                          market=data))
    elif context.data is not data:
        raise ValueError("context.data must be the market data being backtested")
    rules = MarketRules.load(config.market_rules_path)
    started = time.perf_counter()
    strategy = build_strategy(config, context)
    timings["strategy_setup_seconds"] = round(time.perf_counter() - started, 2)
    schedule = build_schedule(config.schedule)
    started = time.perf_counter()
    result = BacktestEngine(config, data, rules, strategy, schedule).run()
    timings["engine_seconds"] = round(time.perf_counter() - started, 2)

    if hasattr(strategy, "report_tables"):
        started = time.perf_counter()
        result.extras = strategy.report_tables()
        timings["report_tables_seconds"] = round(time.perf_counter() - started, 2)

    calendar = TradingCalendar(data.sessions)
    start = calendar.index_on_or_after(config.start)
    end = calendar.index_on_or_before(config.end)
    nav = to_series(result.nav, "nav_fen") / config.initial_capital_fen
    values = {"策略": nav}
    values.update(benchmarks_for(data, start, end, strategy.eligible, schedule, config.index_benchmarks,
                                 config.equal_weight_benchmark))
    perf = {name: performance(series, config.periods_per_year, config.risk_free_rate) for name, series in values.items()}
    rel = {name: relative(nav, series, config.periods_per_year) for name, series in values.items() if name != "策略"}
    zero_impact = float(result.delistings["last_close_value_fen"].sum()) / 100 if not result.delistings.empty else 0.0
    summary = {
        "run_id": None,
        "name": config.name,
        "config_hash": config.config_hash,
        "rules_sha256": file_sha256(config.market_rules_path),
        "code_version": code_version(ROOT),
        "data": {
            "source": data.metadata.get("source"),
            "data_version": data.metadata.get("data_version"),
            "catalog_run_id": data.metadata.get("catalog_run_id"),
            "fingerprint": data.fingerprint(),
            "shape": list(data.shape),
        },
        "strategy": {"id": config.strategy_id, "version": config.strategy_version,
                     "params": asdict(strategy.params)},
        "performance": perf,
        "relative": rel,
        "trading": trading_stats(result.nav, result.fills, config.periods_per_year),
        "delisting": {"settlement": config.delisting_settlement,
                      "zero_settlement_impact_cny": zero_impact if config.delisting_settlement == "last_close" else 0.0},
        "counters": result.counters,
        "checks": result.checks,
        "timings": timings,
        "limitations": LIMITATIONS,
    }
    return result, summary, values


def write_artifacts(root: Path, result: BacktestResult, summary: dict[str, Any],
                    values: dict[str, pd.Series], directory: Path | None = None) -> Path:
    """Write a run's tables, manifest and report (a new directory unless given)."""
    if directory is None:
        run_id, directory = create_artifact_dir(root / "artifacts" / "backtests")
    else:
        run_id = directory.name
    summary = {**summary, "run_id": run_id, "config": result.config.payload}
    for name in ("nav", "positions", "orders", "fills", "rejections", "corporate_actions", "delistings",
                 "signals", "skipped"):
        frame = getattr(result, name)
        if not frame.empty:
            write_parquet_atomic(frame, directory / f"{name}.parquet")
    curves = pd.DataFrame(values)
    curves.index.name = "session"
    write_parquet_atomic(curves.reset_index(), directory / "curves.parquet")
    for name, frame in result.extras.items():
        if not frame.empty:
            write_parquet_atomic(frame, directory / f"{name}.parquet")
    json_dump(directory / "manifest.json", summary)
    report = render_report(summary, values, result)
    if result.extras:
        from ..analytics.risk import render_risk_section

        report += render_risk_section(result.extras)
    (directory / "report.md").write_text(report, encoding="utf-8")
    return directory


def summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, default=str)
