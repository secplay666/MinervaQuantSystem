"""Backtest experiments: registered single runs, parameter sweeps and stress
scenarios (ADR-009).  Shares the experiment registry and out-of-sample gate
with factor evaluation."""

from __future__ import annotations

import copy
import itertools
import json
import logging
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data_platform.storage import write_parquet_atomic
from ..data_platform.utils import code_version, create_artifact_dir, json_dump
from ..features.store import code_hash
from .data import ResearchData, load_research_data
from .experiments import ROOT, SAMPLES, ResearchConfig
from .registry import Registry

LOGGER = logging.getLogger(__name__)


def get_path(payload: dict[str, Any], dotted: str) -> Any:
    node: Any = payload
    for key in dotted.split("."):
        node = node[key]
    return node


def set_path(payload: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    node = payload
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    return payload


def _scaled(value: Any, factor: int) -> str:
    return str(Fraction(str(value)) * factor)


# Each scenario maps a config payload to a modified copy (ADR-006, ADR-009).
STRESS_SCENARIOS: dict[str, Any] = {
    "fees_x2": lambda p: set_path(p, "costs.commission_rate", _scaled(get_path(p, "costs.commission_rate"), 2)),
    "slippage_20bp": lambda p: set_path(p, "execution.slippage_bps", "20"),
    "delay_t2": lambda p: set_path(p, "execution.delay_sessions", 2),
    "delisting_zero": lambda p: set_path(p, "delisting.settlement", "zero"),
    "capital_1m_50_names": lambda p: set_path(set_path(p, "run.initial_capital_cny", "1000000"),
                                             "strategy.params.construction.n_holdings", 50),
    "capital_100m": lambda p: set_path(p, "run.initial_capital_cny", "100000000"),
    "universe_800": lambda p: set_path(p, "strategy.params.universe.size", 800),
    # Timeliness (ADR-004): every financial statement usable 20 sessions later.
    "fundamentals_lag_20": lambda p: set_path(p, "fundamentals.extra_lag_sessions", 20),
}


def backtest_metrics(summary: dict[str, Any]) -> dict[str, float | None]:
    strategy = summary["performance"]["策略"]
    metrics: dict[str, Any] = {f"strategy.{key}": strategy.get(key)
                               for key in ("total_return", "cagr", "volatility", "sharpe", "max_drawdown")}
    for name, values in summary["relative"].items():
        prefix = "ew" if name == "等权全收益基准" else name.split("（")[0]
        for key in ("excess_cagr", "tracking_error", "information_ratio"):
            metrics[f"{prefix}.{key}"] = values.get(key)
    for key in ("annual_turnover", "annual_cost_drag", "fees_cny"):
        metrics[f"trading.{key}"] = summary["trading"].get(key)
    return {key: (float(value) if isinstance(value, (int, float)) and np.isfinite(value) else None)
            for key, value in metrics.items()}


@dataclass
class LoadedData:
    market: Any
    research: ResearchData


def data_key(payload: dict[str, Any], root: Path = ROOT) -> tuple:
    return (payload["data"]["source"], str((root / payload["data"]["path"]).resolve()),
            int(payload.get("fundamentals", {}).get("extra_lag_sessions", 0)))


def load_data_for(payload: dict[str, Any], root: Path = ROOT, market: Any = None) -> LoadedData:
    from ..backtest.market_data import load_market_data

    source, path, lag = data_key(payload, root)
    market = market or load_market_data(source, Path(path))
    return LoadedData(market, load_research_data(source, Path(path), market=market, fundamentals_lag_sessions=lag))


def _experiment(payload: dict[str, Any], registry: Registry, root: Path) -> tuple[ResearchConfig, str]:
    research_config = ResearchConfig.load(root / payload["experiment"]["research_config"], root)
    windows = research_config.windows()
    experiment_id = registry.ensure_experiment(research_config.experiment, hypothesis=research_config.hypothesis,
                                               is_period=windows["IS"], oos_period=windows["OOS"])
    return research_config, experiment_id


def run_backtest_experiment(payload: dict[str, Any], sample: str = "IS", *, kind: str = "backtest",
                            confirm_oos: bool = False, reason: str = "", force: bool = False,
                            registry: Registry | None = None, root: Path = ROOT, loaded: LoadedData | None = None,
                            sweep_id: str | None = None) -> tuple[Path, dict[str, Any]]:
    """One registered backtest over the experiment's sample window."""
    from ..backtest.config import BacktestConfig
    from ..backtest.runner import run_backtest, write_artifacts
    from ..strategy.registry import StrategyContext

    sample = sample.upper()
    if sample not in SAMPLES:
        raise ValueError(f"sample must be one of {SAMPLES}")
    if sample == "OOS" and not confirm_oos:
        raise PermissionError("out-of-sample backtests need --confirm-oos (allowed once per config)")
    registry = registry or Registry(root / "artifacts" / "registry.sqlite")
    research_config, experiment_id = _experiment(payload, registry, root)
    payload = copy.deepcopy(payload)
    start, end = research_config.samples[sample]
    payload["run"] = {**payload["run"], "start": start.isoformat(), "end": end.isoformat()}
    config = BacktestConfig.from_payload(payload, root)
    run_id, directory = create_artifact_dir(root / "artifacts" / "backtests")
    if sample == "OOS":
        registry.open_out_of_sample(experiment_id, config.config_hash, run_id, reason=reason, force=force)
    loaded = loaded or load_data_for(payload, root)
    version = code_version(root)
    registry.start_run(
        run_id, kind, experiment_id=experiment_id, sweep_id=sweep_id, config_hash=config.config_hash,
        config_json=payload, strategy_id=config.strategy_id, strategy_version=config.strategy_version,
        git_sha=version.get("git_sha"), dirty=version.get("dirty"), features_hash=code_hash(),
        data_version=loaded.research.metadata.get("data_version"), market_fp=loaded.market.fingerprint(),
        research_fp=loaded.research.fingerprint(), sample=sample, period_start=start.isoformat(),
        period_end=end.isoformat(), artifacts_path=str(directory))
    try:
        context = StrategyContext(root, loaded.market, lambda: loaded.research)
        result, summary, values = run_backtest(config, loaded.market, context)
        summary = {**summary, "experiment": {"id": experiment_id, "sample": sample, "kind": kind,
                                             "in_sample_runs_before": registry.trial_count(experiment_id)}}
        write_artifacts(root, result, summary, values, directory)
    except Exception as exc:
        registry.finish_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    registry.finish_run(run_id, metrics=backtest_metrics(summary))
    return directory, summary


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = sorted(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[key] for key in keys))]


def run_sweep(payload: dict[str, Any], grid: dict[str, list[Any]], *, registry: Registry | None = None,
              root: Path = ROOT) -> tuple[Path, pd.DataFrame]:
    """Every combination of ``grid`` (dotted payload path -> values) as a
    registered in-sample trial; the data is loaded once."""
    registry = registry or Registry(root / "artifacts" / "registry.sqlite")
    _, experiment_id = _experiment(payload, registry, root)
    trials = expand_grid(grid)
    sweep_id, directory = create_artifact_dir(root / "artifacts" / "sweeps")
    registry.record_sweep(sweep_id, experiment_id, grid, len(trials))
    loaded = load_data_for(payload, root)
    rows = []
    for number, overrides in enumerate(trials, 1):
        trial = copy.deepcopy(payload)
        for path, value in overrides.items():
            set_path(trial, path, value)
        LOGGER.info("sweep %s trial %d/%d: %s", sweep_id, number, len(trials), overrides)
        run_dir, summary = run_backtest_experiment(trial, "IS", kind="trial", registry=registry, root=root,
                                                   loaded=loaded, sweep_id=sweep_id)
        rows.append({"trial": number, **{f"param:{k}": json.dumps(v) for k, v in overrides.items()},
                     **backtest_metrics(summary), "run": run_dir.name})
    table = pd.DataFrame(rows)
    write_parquet_atomic(table, directory / "trials.parquet")
    json_dump(directory / "manifest.json", {"sweep_id": sweep_id, "experiment_id": experiment_id, "grid": grid,
                                            "trials": len(trials), "base_config": payload})
    report = render_sweep_report(sweep_id, grid, table, registry.trial_count(experiment_id))
    (directory / "report.md").write_text(report, encoding="utf-8")
    return directory, table


def run_stress(payload: dict[str, Any], scenarios: list[str], sample: str = "IS", *, confirm_oos: bool = False,
               reason: str = "", registry: Registry | None = None, root: Path = ROOT) -> pd.DataFrame:
    unknown = set(scenarios) - set(STRESS_SCENARIOS)
    if unknown:
        raise ValueError(f"unknown stress scenarios {sorted(unknown)}; known: {sorted(STRESS_SCENARIOS)}")
    registry = registry or Registry(root / "artifacts" / "registry.sqlite")
    cache: dict[tuple, LoadedData] = {}
    rows = []
    for name in scenarios:
        trial = STRESS_SCENARIOS[name](copy.deepcopy(payload))
        key = data_key(trial, root)
        if key not in cache:  # the market panels are shared; research data depends on the lag
            market = next(iter(cache.values())).market if cache else None
            cache[key] = load_data_for(trial, root, market)
        run_dir, summary = run_backtest_experiment(trial, sample, kind="stress", confirm_oos=confirm_oos,
                                                   reason=reason or f"stress {name}", registry=registry, root=root,
                                                   loaded=cache[key])
        rows.append({"scenario": name, **backtest_metrics(summary), "run": run_dir.name})
    return pd.DataFrame(rows)


def _pct(value: Any) -> str:
    return "—" if value is None or not np.isfinite(float(value)) else f"{float(value):.2%}"


def _num(value: Any) -> str:
    return "—" if value is None or not np.isfinite(float(value)) else f"{float(value):.2f}"


def render_sweep_report(sweep_id: str, grid: dict[str, list[Any]], table: pd.DataFrame, trials_total: int) -> str:
    sharpe = table["strategy.sharpe"].astype(float)
    n = len(table)
    spread = float(sharpe.std()) if n > 1 else 0.0
    expected_max = spread * float(np.sqrt(2 * np.log(n))) if n > 1 else 0.0
    params = [column for column in table.columns if column.startswith("param:")]
    lines = [
        f"# 参数实验 {sweep_id}", "",
        f"- 参数网格：`{json.dumps(grid, ensure_ascii=False)}`；本次 {n} 次试验，本实验累计样本内运行 {trials_total} 次。",
        f"- 夏普比率：最高 {sharpe.max():.2f}，中位数 {sharpe.median():.2f}，试验间标准差 {spread:.2f}。"
        f"即使各组参数其实没有差别，{n} 次试验中的最高夏普也约比平均值高 {expected_max:.2f}，"
        "所以最高值本身不能作为结论。", "",
        "| 试验 | " + " | ".join(p.removeprefix("param:") for p in params)
        + " | 年化 | 夏普 | 最大回撤 | 相对等权超额 | 信息比率 | 年换手 |",
        "|---:|" + "---|" * len(params) + "---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in table.sort_values("strategy.sharpe", ascending=False).iterrows():
        values = " | ".join(str(row[p]) for p in params)
        lines.append(f"| {row['trial']} | {values} | {_pct(row['strategy.cagr'])} | {_num(row['strategy.sharpe'])} | "
                     f"{_pct(row['strategy.max_drawdown'])} | {_pct(row.get('ew.excess_cagr'))} | "
                     f"{_num(row.get('ew.information_ratio'))} | {_num(row.get('trading.annual_turnover'))} |")
    return "\n".join(lines) + "\n"
