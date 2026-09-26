"""Stage-3 plumbing: portfolio-state hook, schedules, registry, artifact ids."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from bt_fakes import SyntheticMarket, config, rules, weekdays
from quant_system.backtest.engine import BacktestEngine
from quant_system.data_platform.utils import create_artifact_dir
from quant_system.domain.calendar import TradingCalendar
from quant_system.domain.entities import TargetPortfolio
from quant_system.strategy.base import EveryNSessionsSchedule, MonthEndSchedule, build_schedule
from quant_system.strategy.registry import StrategyContext, build_strategy, registered

DAYS = weekdays(date(2024, 1, 2), 30)


@dataclass
class StatefulStrategy:
    """Buys on the first signal and records the state it is shown later."""

    wants_portfolio_state = True
    strategy_id: str = "stateful"
    version: str = "1"
    seen: list = field(default_factory=list)

    def on_close(self, view, state) -> TargetPortfolio:
        self.seen.append(state)
        return TargetPortfolio(view.session, {"600001": 0.5, "000001": 0.3}, {"600001": {"rank": 1},
                                                                            "000001": {"rank": 2}})

    def eligible(self, view):
        return view.has_bar()


def test_stateful_strategy_sees_holdings_marked_at_the_close() -> None:
    closes = {"600001": [10.0] * 10 + [None] * 5 + [11.0] * 15,  # suspended: marked at the reference price
              "000001": [20.0] * 30}
    market = SyntheticMarket(DAYS, closes)
    strategy = StatefulStrategy()
    schedule = build_schedule({"type": "every_n_sessions", "every": 6})
    cfg = config(run={"start": str(DAYS[0]), "end": str(DAYS[-1]), "initial_capital_cny": "1000000"})
    result = BacktestEngine(cfg, market.build(), rules(), strategy, schedule).run()
    assert [s.session for s in strategy.seen] == [DAYS[i] for i in (5, 11, 17, 23, 29)]
    assert strategy.seen[0].previous_target is None and not strategy.seen[0].quantities
    state = strategy.seen[1]  # DAYS[11]: 600001 suspended since DAYS[10]
    nav = result.nav.set_index("session").loc[state.session]
    positions = result.positions[result.positions["session"] == state.session].set_index("symbol")
    assert state.nav_fen == nav["nav_fen"] and state.cash_fen == nav["cash_fen"]
    assert dict(state.quantities) == positions["quantity"].to_dict()
    for symbol, weight in state.weights.items():
        assert weight == pytest.approx(positions.loc[symbol, "value_fen"] / nav["nav_fen"], abs=0)
    assert sum(state.weights.values()) + state.cash_fen / state.nav_fen == pytest.approx(1.0)
    assert state.previous_target.as_of == DAYS[5]


def test_schedules_from_config() -> None:
    calendar = TradingCalendar(DAYS)
    assert isinstance(build_schedule(None), MonthEndSchedule)
    every = build_schedule({"type": "every_n_sessions", "every": 10})
    assert isinstance(every, EveryNSessionsSchedule)
    assert [i for i in range(len(DAYS)) if every.is_rebalance(calendar, i)] == [9, 19, 29]
    with pytest.raises(ValueError, match="unknown schedule"):
        build_schedule({"type": "weekly"})
    assert config(schedule={"type": "every_n_sessions", "every": 5}).schedule["every"] == 5


def test_registry_builds_known_strategies_and_rejects_unknown() -> None:
    assert "momentum" in registered()
    context = StrategyContext(root=None, data=None)
    strategy = build_strategy(config(strategy={"id": "momentum", "version": "2", "params": {"top_n": 5}}), context)
    assert strategy.params.top_n == 5 and strategy.version == "2"
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy(config(strategy={"id": "nope", "params": {}}), context)
    with pytest.raises(ValueError, match="needs research data"):
        context.research()


def test_artifact_dirs_never_collide(tmp_path) -> None:
    ids = [create_artifact_dir(tmp_path)[0] for _ in range(100)]
    assert len(set(ids)) == 100
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(ids)


def test_registry_records_runs_trials_and_guards_out_of_sample(tmp_path) -> None:
    from quant_system.research.registry import OutOfSampleAlreadyUsed, Registry

    registry = Registry(tmp_path / "registry.sqlite")
    windows = {"is_period": ("2021-01-01", "2023-12-31"), "oos_period": ("2024-01-01", "2026-09-30")}
    experiment = registry.ensure_experiment("mf", hypothesis="h", **windows)
    assert registry.ensure_experiment("mf") == experiment == registry.ensure_experiment("mf", **windows)
    with pytest.raises(ValueError, match="already registered"):
        registry.ensure_experiment("mf", is_period=("2021-01-01", "2024-06-30"), oos_period=windows["oos_period"])
    for i in range(3):
        registry.start_run(f"r{i}", "trial", experiment_id=experiment, config_hash=f"h{i}",
                           config_json={"a": i}, sample="IS", dirty=False)
        registry.finish_run(f"r{i}", metrics={"sharpe": 0.1 * i})
    registry.start_run("r-failed", "trial", experiment_id=experiment, sample="IS")
    registry.finish_run("r-failed", status="failed", error="boom")
    assert registry.trial_count(experiment) == 3
    assert registry.metrics(["r2"])["value"].tolist() == [pytest.approx(0.2)]
    assert registry.runs(experiment)["status"].tolist().count("complete") == 3
    registry.open_out_of_sample(experiment, "h1", "oos-1", reason="final")
    with pytest.raises(OutOfSampleAlreadyUsed):
        registry.open_out_of_sample(experiment, "h1", "oos-2")
    with pytest.raises(ValueError, match="needs a reason"):
        registry.open_out_of_sample(experiment, "h1", "oos-2", force=True)
    registry.open_out_of_sample(experiment, "h1", "oos-2", force=True, reason="data correction")
    accesses = registry.out_of_sample_accesses(experiment)
    assert accesses["forced"].tolist() == [0, 1]
