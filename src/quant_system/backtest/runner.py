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
from ..data_platform.utils import code_version, file_sha256, json_dump, unique_run_id
from ..domain.calendar import TradingCalendar
from ..domain.rules import MarketRules
from ..strategy.base import MonthEndSchedule
from ..strategy.momentum import MomentumParams, MomentumStrategy
from .config import BacktestConfig
from .engine import BacktestEngine, BacktestResult
from .market_data import MarketData, load_market_data

LOGGER = logging.getLogger(__name__)


def build_strategy(config: BacktestConfig):
    if config.strategy_id == "momentum":
        params = dict(config.strategy_params)
        if "boards" in params:
            params["boards"] = tuple(params["boards"])
        return MomentumStrategy(MomentumParams(**params), version=config.strategy_version)
    raise ValueError(f"unknown strategy {config.strategy_id!r}")


def run_backtest(config: BacktestConfig, data: MarketData | None = None) -> tuple[BacktestResult, dict[str, Any],
                                                                                     dict[str, pd.Series]]:
    timings: dict[str, float] = {}
    started = time.perf_counter()
    if data is None:
        data = load_market_data(config.data_source, config.data_path)
    timings["load_seconds"] = round(time.perf_counter() - started, 2)
    rules = MarketRules.load(config.market_rules_path)
    strategy = build_strategy(config)
    schedule = MonthEndSchedule()
    started = time.perf_counter()
    result = BacktestEngine(config, data, rules, strategy, schedule).run()
    timings["engine_seconds"] = round(time.perf_counter() - started, 2)

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
        "code_version": code_version(Path(__file__).resolve().parents[3]),
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
                    values: dict[str, pd.Series]) -> Path:
    run_id = unique_run_id(root)
    directory = root / "artifacts" / "backtests" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    summary = {**summary, "run_id": run_id, "config": result.config.payload}
    for name in ("nav", "positions", "orders", "fills", "rejections", "corporate_actions", "delistings",
                 "signals", "skipped"):
        frame = getattr(result, name)
        if not frame.empty:
            write_parquet_atomic(frame, directory / f"{name}.parquet")
    curves = pd.DataFrame(values)
    curves.index.name = "session"
    write_parquet_atomic(curves.reset_index(), directory / "curves.parquet")
    json_dump(directory / "manifest.json", summary)
    (directory / "report.md").write_text(render_report(summary, values, result), encoding="utf-8")
    return directory


def summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2, default=str)
