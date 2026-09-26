"""Research runs: configuration, factor evaluation, registry bookkeeping.

A research config fixes the data, universe, processing, factor list and the
in-sample / out-of-sample windows of an experiment.  Every run is recorded
in the experiment registry before it starts; an out-of-sample evaluation
must be requested explicitly and is allowed once per configuration.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data_platform.storage import write_parquet_atomic
from ..data_platform.utils import code_version, create_artifact_dir, json_dump, json_hash
from ..domain.calendar import TradingCalendar
from ..domain.rules import MarketRules
from ..evaluation.factor_eval import EvaluationSpec, evaluate_factors, forward_returns, sample_rows
from ..features.context import FactorContext
from ..features.processing import ProcessingSpec
from ..features.registry import all_factors, get
from ..features.store import FactorCache, build_factor_panel, code_hash
from ..strategy.base import build_schedule
from ..universe.liquidity import UniverseSpec, universe_masks
from .data import ResearchData, load_research_data
from .registry import Registry

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ("IS", "OOS")


@dataclass(frozen=True)
class ResearchConfig:
    name: str
    experiment: str
    hypothesis: str
    data_source: str
    data_path: Path
    market_rules_path: Path
    schedule: dict[str, Any]
    universe: UniverseSpec
    processing: ProcessingSpec
    factors: tuple[str, ...]
    samples: dict[str, tuple[date, date]]
    horizons: tuple[int, ...]
    quantiles: int
    min_names: int
    fundamentals_lag_sessions: int
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        # Hash the resolved factor list: "all" grows as factors are added, and
        # the out-of-sample gate must treat a larger factor set as a new config.
        return json_hash({**self.payload, "factors": list(self.factors)})

    @classmethod
    def load(cls, path: Path, root: Path = ROOT) -> "ResearchConfig":
        return cls.from_payload(json.loads(Path(path).read_text(encoding="utf-8")), root)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], root: Path = ROOT) -> "ResearchConfig":
        factors = payload.get("factors", "all")
        ids = tuple(spec.id for spec in all_factors()) if factors == "all" else tuple(factors)
        for factor_id in ids:
            get(factor_id)  # unknown ids fail at load time
        samples = {key.upper(): (date.fromisoformat(value[0]), date.fromisoformat(value[1]))
                   for key, value in payload["samples"].items()}
        if set(samples) != set(SAMPLES):
            raise ValueError("samples must define exactly 'is' and 'oos'")
        if samples["IS"][1] >= samples["OOS"][0]:
            raise ValueError("the in-sample window must end before the out-of-sample window starts")
        evaluation = payload.get("evaluation", {})
        return cls(
            name=payload["name"], experiment=payload["experiment"], hypothesis=payload.get("hypothesis", ""),
            data_source=payload["data"]["source"], data_path=(root / payload["data"]["path"]).resolve(),
            market_rules_path=(root / payload["market_rules"]).resolve(),
            schedule=dict(payload.get("schedule", {"type": "month_end"})),
            universe=UniverseSpec.from_payload(payload.get("universe")),
            processing=ProcessingSpec.from_payload(payload.get("processing")),
            factors=ids, samples=samples,
            horizons=tuple(int(h) for h in evaluation.get("horizons", (1, 2, 3, 6))),
            quantiles=int(evaluation.get("quantiles", 5)), min_names=int(evaluation.get("min_names", 30)),
            fundamentals_lag_sessions=int(payload.get("fundamentals", {}).get("extra_lag_sessions", 0)),
            payload=payload,
        )

    def windows(self) -> dict[str, tuple[str, str]]:
        return {key: (start.isoformat(), end.isoformat()) for key, (start, end) in self.samples.items()}


def schedule_rows(sessions: tuple[date, ...], schedule: dict[str, Any]) -> np.ndarray:
    """Rebalance rows over the whole calendar (not just a sample window)."""
    calendar = TradingCalendar(sessions)
    rule = build_schedule(schedule)
    return np.array([i for i in range(len(sessions)) if rule.is_rebalance(calendar, i)], dtype=np.int64)


def periods_per_year(sessions: tuple[date, ...], rows: np.ndarray) -> float:
    if len(rows) < 2:
        return 12.0
    years = (sessions[rows[-1]] - sessions[rows[0]]).days / 365.25
    return (len(rows) - 1) / years if years > 0 else 12.0


def run_factor_evaluation(config: ResearchConfig, sample: str = "IS", *, confirm_oos: bool = False,
                          reason: str = "", force: bool = False, research: ResearchData | None = None,
                          registry: Registry | None = None, root: Path = ROOT,
                          cache: FactorCache | None = None) -> tuple[Path, dict[str, pd.DataFrame]]:
    sample = sample.upper()
    if sample not in SAMPLES:
        raise ValueError(f"sample must be one of {SAMPLES}")
    if sample == "OOS" and not confirm_oos:
        raise PermissionError("out-of-sample evaluation needs --confirm-oos (it is allowed once per config)")
    registry = registry or Registry(root / "artifacts" / "registry.sqlite")
    windows = config.windows()
    experiment_id = registry.ensure_experiment(config.experiment, hypothesis=config.hypothesis,
                                               is_period=windows["IS"], oos_period=windows["OOS"])
    run_id, directory = create_artifact_dir(root / "artifacts" / "research")
    if sample == "OOS":
        registry.open_out_of_sample(experiment_id, config.config_hash, run_id, reason=reason, force=force)
    timings: dict[str, float] = {}
    started = time.perf_counter()
    research = research or load_research_data(config.data_source, config.data_path,
                                              fundamentals_lag_sessions=config.fundamentals_lag_sessions)
    timings["load_seconds"] = round(time.perf_counter() - started, 2)
    version = code_version(root)
    start, end = config.samples[sample]
    registry.start_run(
        run_id, "factor_eval", experiment_id=experiment_id, config_hash=config.config_hash,
        config_json=config.payload, git_sha=version.get("git_sha"), dirty=version.get("dirty"),
        features_hash=code_hash(), data_version=research.metadata.get("data_version"),
        market_fp=research.market.fingerprint(), research_fp=research.fingerprint(), sample=sample,
        period_start=start.isoformat(), period_end=end.isoformat(), artifacts_path=str(directory))
    try:
        results = _evaluate(config, sample, research, cache or FactorCache(root / "data" / "features"), timings)
    except Exception as exc:
        registry.finish_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    for name, frame in results.items():
        if name != "correlation":
            write_parquet_atomic(frame, directory / f"{name}.parquet")
    write_parquet_atomic(results["correlation"].reset_index(names="factor"), directory / "correlation.parquet")
    trials = registry.trial_count(experiment_id) + 1
    manifest = {"run_id": run_id, "kind": "factor_eval", "experiment_id": experiment_id,
                "config_hash": config.config_hash, "config": config.payload, "sample": sample,
                "window": [start.isoformat(), end.isoformat()], "code_version": version,
                "features_hash": code_hash(), "data_version": research.metadata.get("data_version"),
                "research_fingerprint": research.fingerprint(), "timings": timings,
                "trials_in_experiment": trials}
    json_dump(directory / "manifest.json", manifest)
    from ..analytics.factor_report import render_factor_report

    (directory / "report.md").write_text(render_factor_report(manifest, results), encoding="utf-8")
    metrics = {}
    for row in results["summary"].itertuples(index=False):
        metrics[f"{row.factor}.rank_ic_mean"] = row.rank_ic_mean
        metrics[f"{row.factor}.rank_ic_t"] = row.rank_ic_t
    registry.finish_run(run_id, metrics=metrics)
    return directory, results


def _evaluate(config: ResearchConfig, sample: str, research: ResearchData, cache: FactorCache,
              timings: dict[str, float]) -> dict[str, pd.DataFrame]:
    start, end = config.samples[sample]
    spec = EvaluationSpec(sample=sample, start=start, end=end, horizons=config.horizons,
                          quantiles=config.quantiles, min_names=config.min_names)
    sessions = research.sessions
    rows = schedule_rows(sessions, config.schedule)
    signals = sample_rows(sessions, rows, spec)
    if len(signals) == 0:
        raise ValueError(f"no rebalance rows in the {sample} window {start}..{end}")
    ctx = FactorContext(research)
    started = time.perf_counter()
    universe, diagnostics = universe_masks(ctx, signals, config.universe)
    timings["universe_seconds"] = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    panel = build_factor_panel(research, signals, list(config.factors), universe, config.processing, cache, ctx)
    timings["factor_seconds"] = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    rules = MarketRules.load(config.market_rules_path)
    forward = forward_returns(ctx, rules, rows, signals, universe, spec)
    results = evaluate_factors(panel, forward, spec, periods_per_year(sessions, rows))
    timings["evaluation_seconds"] = round(time.perf_counter() - started, 2)
    results["universe"] = diagnostics
    for frame in (results["series"], results["quantiles"]):
        if not frame.empty:
            frame.insert(1, "session", [sessions[int(t)] for t in frame["row"]])
    return results
