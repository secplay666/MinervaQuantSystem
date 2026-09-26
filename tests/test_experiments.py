"""Stage-3 P6: registered backtest experiments, sweeps and stress scenarios."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from research_fakes import synthetic_frames
from quant_system.research.backtests import STRESS_SCENARIOS, run_backtest_experiment, run_stress, run_sweep
from quant_system.research.registry import OutOfSampleAlreadyUsed, Registry

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path):
    market, research = synthetic_frames(41, sessions=400, symbols=40)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    for name, frame in {**market, **research}.items():
        frame.to_parquet(fixture / f"{name}.parquet", index=False)
    (tmp_path / "configs" / "market_rules").mkdir(parents=True)
    shutil.copy(REPO / "configs" / "market_rules" / "cn_a_share.json", tmp_path / "configs" / "market_rules")
    (tmp_path / "configs" / "research").mkdir()
    research_config = {
        "name": "synthetic", "experiment": "synthetic_mf", "data": {"source": "parquet_dir", "path": "fixture"},
        "market_rules": "configs/market_rules/cn_a_share.json", "factors": ["vol_60", "rev_1m", "turn_20"],
        "samples": {"is": ["2020-12-01", "2021-03-31"], "oos": ["2021-04-01", "2021-07-31"]},
    }
    (tmp_path / "configs" / "research" / "synthetic.json").write_text(json.dumps(research_config), encoding="utf-8")
    payload = {
        "name": "synthetic_mf", "experiment": {"research_config": "configs/research/synthetic.json"},
        "run": {"start": "2020-01-01", "end": "2021-12-31", "initial_capital_cny": "10000000"},
        "data": {"source": "parquet_dir", "path": "fixture"},
        "market_rules": "configs/market_rules/cn_a_share.json",
        "strategy": {"id": "multifactor", "version": "1", "params": {
            "factors": ["vol_60", "rev_1m", "turn_20", "amihud_20"], "min_families": 2,
            "universe": {"size": 25, "min_listing_sessions": 60, "coverage_window": 20},
            "processing": {"min_names": 5}, "use_cache": False,
            "construction": {"n_holdings": 6, "max_weight": 0.3, "industry_deviation": 0.2}}},
        "schedule": {"type": "month_end"},
        "execution": {"price": "open", "slippage_bps": "5", "max_participation": 0.1, "delay_sessions": 1},
        "costs": {"commission_rate": "0.00025", "commission_min_cny": "5"},
        "benchmarks": {"indices": [], "equal_weight_universe": True},
    }
    return tmp_path, payload, Registry(tmp_path / "artifacts" / "registry.sqlite")


def test_experiment_runs_are_registered_and_the_oos_window_opens_once(workspace) -> None:
    root, payload, registry = workspace
    directory, summary = run_backtest_experiment(payload, "IS", registry=registry, root=root)
    assert (directory / "report.md").exists() and summary["experiment"]["sample"] == "IS"
    assert str(summary["performance"]["策略"]["start"]) >= "2020-12-01"
    runs = registry.runs()
    assert runs["status"].tolist() == ["complete"] and runs["kind"].tolist() == ["backtest"]
    assert "strategy.sharpe" in set(registry.metrics()["name"])
    with pytest.raises(PermissionError):
        run_backtest_experiment(payload, "OOS", registry=registry, root=root)
    run_backtest_experiment(payload, "OOS", confirm_oos=True, reason="final", registry=registry, root=root)
    with pytest.raises(OutOfSampleAlreadyUsed):
        run_backtest_experiment(payload, "OOS", confirm_oos=True, registry=registry, root=root)


def test_sweep_records_every_trial(workspace) -> None:
    root, payload, registry = workspace
    directory, table = run_sweep(payload, {"strategy.params.construction.n_holdings": [4, 6],
                                           "strategy.params.weighting": ["family_equal"]}, registry=registry, root=root)
    assert len(table) == 2 and (directory / "report.md").exists()
    assert registry.trial_count(registry.runs()["experiment_id"].iloc[0]) == 2
    assert set(registry.runs()["kind"]) == {"trial"}


def test_stress_scenarios_change_what_they_claim(workspace) -> None:
    root, payload, registry = workspace
    assert set(STRESS_SCENARIOS) >= {"fees_x2", "slippage_20bp", "delay_t2", "capital_1m_50_names"}
    table = run_stress(payload, ["fees_x2", "delay_t2"], "IS", registry=registry, root=root).set_index("scenario")
    base_dir, base = run_backtest_experiment(payload, "IS", registry=registry, root=root)
    assert table.loc["fees_x2", "trading.fees_cny"] > base["trading"]["fees_cny"]
    assert set(registry.runs()["kind"]) == {"stress", "backtest"}
