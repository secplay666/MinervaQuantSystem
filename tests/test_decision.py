"""Stage-4 P1: decision job (ADR-008, docs/design/stage4-app-design.md §2)."""

from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from quant_system.app.db import Base, make_engine, open_database, upgrade  # noqa: E402
from quant_system.app.db.models import Approval, DecisionRun, Event, OrderIntent, TargetPosition  # noqa: E402
from quant_system.backtest.config import BacktestConfig  # noqa: E402
from quant_system.backtest.market_data import load_market_data  # noqa: E402
from quant_system.backtest.runner import run_backtest  # noqa: E402
from quant_system.data_platform.sessions import SHANGHAI_TZ  # noqa: E402
from quant_system.decision import gates as G  # noqa: E402
from quant_system.decision.accounts import add_event, create_account, replay, reverse_event  # noqa: E402
from quant_system.decision.inputs import IngestRecord, is_month_end, is_rebalance_day, latest_ingest  # noqa: E402
from quant_system.decision.job import DecisionLoaders, run_daily  # noqa: E402
from quant_system.decision.reference import FrameReference  # noqa: E402
from quant_system.ledger import CashAdjusted, CashDeposited, Ledger, LedgerInvariantError, PositionAdjusted  # noqa: E402
from quant_system.research.data import load_research_data  # noqa: E402
from quant_system.strategy.registry import StrategyContext  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "multifactor_cn"
CONFIG = "configs/strategies/golden_multifactor.json"

pytestmark = pytest.mark.skipif(not (FIXTURE / "SOURCE.json").exists(), reason="multi-factor fixture not extracted")


# -- units ----------------------------------------------------------------------------------


def test_manual_adjustments_replay_and_keep_cash_non_negative() -> None:
    ledger = Ledger()
    ledger.apply(CashDeposited("d", date(2026, 9, 1), 1_000))
    ledger.apply(PositionAdjusted("a", date(2026, 9, 1), "600000", 0, 300, 2_500))
    assert ledger.positions["600000"].sellable == 300  # entered holdings count as settled
    ledger.apply(CashAdjusted("c", date(2026, 9, 2), -400))
    assert ledger.cash_fen == 600 and ledger.replayed().positions == ledger.positions
    with pytest.raises(LedgerInvariantError):
        ledger.apply(CashAdjusted("c2", date(2026, 9, 2), -601))
    with pytest.raises(LedgerInvariantError):
        ledger.apply(PositionAdjusted("a2", date(2026, 9, 2), "600000", 100, 0, 0))  # stale old quantity


def test_migrations_create_exactly_the_model_schema(tmp_path: Path) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    db = tmp_path / "app.sqlite"
    upgrade(db)
    upgrade(db)  # idempotent
    with make_engine(db).connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_month_end_uses_the_published_calendar() -> None:
    calendar = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8), date(2026, 12, 31)]
    assert is_month_end(calendar, date(2026, 9, 30)) and not is_month_end(calendar, date(2026, 9, 29))
    assert is_month_end(calendar, date(2026, 12, 31))  # next year's calendar not published yet
    with pytest.raises(ValueError):
        is_month_end(calendar[:3], date(2026, 9, 29) + timedelta(days=1))  # truncated mid-year: refuse
    sessions = tuple(calendar)
    assert is_rebalance_day({"type": "every_n_sessions", "every": 2}, calendar, sessions, date(2026, 9, 29))


def test_latest_ingest_skips_rebuild_manifests(tmp_path: Path) -> None:
    manifests = tmp_path / "data" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "20260926T100000Z.json").write_text(
        '{"run_id": "20260926T100000Z", "status": "complete", "mode": "incremental", '
        '"expected_latest_date": "2026-09-24", "data_version": "v1"}', encoding="utf-8")
    (manifests / "20260926T120000Z.json").write_text(
        '{"run_id": "20260926T120000Z", "status": "complete", "applied": true}', encoding="utf-8")
    record = latest_ingest(tmp_path)
    assert record.run_id == "20260926T100000Z" and record.expected_latest == date(2026, 9, 24)


def test_stale_open_ended_suspensions_are_ignored() -> None:
    day = date(2026, 9, 28)
    frame = pd.DataFrame({
        "symbol": ["old", "today", "until", "resume", "resumed"],
        "suspend_start": [date(2025, 3, 3), day, date(2026, 9, 20), date(2026, 9, 20), date(2026, 9, 1)],
        "suspend_end": [None, None, date(2026, 9, 28), None, None],
        "expected_resume": [date(2025, 3, 17), None, None, date(2026, 10, 9), date(2026, 9, 10)],
    })
    assert FrameReference(suspensions=frame).suspended(day) == {"today", "until", "resume"}


def test_gate_rules() -> None:
    record = IngestRecord("r", "partial", date(2026, 9, 24), "v", "incremental")
    assert not G.g1_ingest(record, date(2026, 9, 24)).passed
    assert not G.g1_ingest(IngestRecord("r", "complete", date(2026, 9, 23), "v", "incremental"),
                           date(2026, 9, 24)).passed
    assert not G.g2_data_version(IngestRecord("r", "complete", None, "v1", "incremental"), "v2").passed
    history = [{"universe": 1800, "scored": 1790}] * 6
    assert G.g3_signal_stability({"universe": 1790, "scored": 1780}, history).passed
    assert not G.g3_signal_stability({"universe": 1200, "scored": 1190}, history).passed
    assert not G.g4_holdings("manual", None, [], date(2026, 9, 24)).passed
    assert not G.g4_holdings("manual", date(2026, 9, 1), ["x"], date(2026, 9, 24)).passed
    assert G.g4_holdings("paper", None, ["x"], date(2026, 9, 24)).passed
    assert G.g5_target({str(k): 0.01 for k in range(100)}, 100).passed
    assert not G.g5_target({str(k): 0.01 for k in range(70)}, 100).passed


def test_reversal_is_refused_when_it_breaks_the_ledger(tmp_path: Path) -> None:
    _, factory = open_database(tmp_path / "app.sqlite")
    with factory() as session:
        create_account(session, ROOT, account_id="m", name="m", mode="manual", strategy_config=CONFIG,
                       initial_cash_fen=1_000_000, start_date=date(2026, 9, 1), actor="test")
        add_event(session, "m", date(2026, 9, 2), "adjustment", {"old_quantity": 0, "new_quantity": 100,
                  "cost_fen": 0}, event_id="adj1", actor="test", symbol="600000")
        add_event(session, "m", date(2026, 9, 3), "adjustment", {"old_quantity": 100, "new_quantity": 200,
                  "cost_fen": 0}, event_id="adj2", actor="test", symbol="600000")
        with pytest.raises(ValueError):
            reverse_event(session, "m", "adj1", trade_date=date(2026, 9, 4), actor="test", reason="x")
        reverse_event(session, "m", "adj2", trade_date=date(2026, 9, 4), actor="test", reason="录入错误")
        assert replay(session, "m").positions["600000"].quantity == 100


# -- the job on the multi-factor fixture ------------------------------------------------------


@pytest.fixture(scope="module")
def loaded():
    market = load_market_data("parquet_dir", FIXTURE)
    research = load_research_data("parquet_dir", FIXTURE, market=market)
    return market, research, pd.read_parquet(FIXTURE / "dividends.parquet")


@pytest.fixture(scope="module")
def golden(loaded):
    market, research, _ = loaded
    config = BacktestConfig.load(ROOT / CONFIG, ROOT)
    result, _, _ = run_backtest(config, market, StrategyContext(ROOT, market, lambda: research))
    return result


def make_root(path: Path) -> Path:
    shutil.copytree(ROOT / "configs", path / "configs")
    return path


def loaders(loaded, day: date, *, status: str = "complete", version: str | None = None,
            suspensions: pd.DataFrame | None = None) -> DecisionLoaders:
    market, research, dividends = loaded
    record = IngestRecord("fixture", status, day, version or market.metadata["data_version"], "incremental")
    return DecisionLoaders(ingest=lambda: record, calendar=lambda _: list(market.sessions), market=lambda: market,
                           research=lambda _: research,
                           reference=lambda _m, _since: FrameReference(dividends, suspensions, None))


def evening(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=SHANGHAI_TZ)


def run(root: Path, loaded, day: date, **kwargs):
    return run_daily(root, db_path=root / "app.sqlite", session_date=day, now=kwargs.pop("now", evening(day)),
                     loaders=loaders(loaded, day, **{k: kwargs.pop(k) for k in ("status", "version", "suspensions")
                                                    if k in kwargs}), **kwargs)


def paper_account(root: Path, start: date, cash_fen: int = 1_000_000_000) -> None:
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        create_account(session, root, account_id="paper", name="模拟账户", mode="paper", strategy_config=CONFIG,
                       initial_cash_fen=cash_fen, start_date=start, actor="test")
        session.commit()


def query(root: Path, statement):
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        return list(session.scalars(statement))


def rebalance_days(golden) -> list[date]:
    return sorted(pd.to_datetime(golden.signals["as_of"]).dt.date.unique())


def test_targets_and_intents_reproduce_the_backtest(tmp_path: Path, loaded, golden) -> None:
    """ADR-008 §12: with the backtest's holdings as the account, the decision's
    target equals the backtest signal of that session, and the intents equal
    the backtest's first-day orders when no corporate action intervenes."""
    market = loaded[0]
    positions, nav, signals, orders = golden.positions, golden.nav, golden.signals, golden.orders
    compared_orders = 0
    for k, day in enumerate(rebalance_days(golden)[4:13:4]):
        root = make_root(tmp_path / f"r{k}")
        held = positions[pd.to_datetime(positions["session"]).dt.date == day]
        cash = int(nav.loc[pd.to_datetime(nav["session"]).dt.date == day, "cash_fen"].iloc[0])
        _, factory = open_database(root / "app.sqlite")
        with factory() as session:
            create_account(session, root, account_id="m", name="m", mode="manual", strategy_config=CONFIG,
                           initial_cash_fen=cash, start_date=day, actor="test")
            for row in held.itertuples():
                add_event(session, "m", day, "adjustment", {"old_quantity": 0, "new_quantity": int(row.quantity),
                          "cost_fen": 0}, event_id=f"h-{row.symbol}", actor="test", symbol=row.symbol)
            session.commit()
        (outcome,) = run(root, loaded, day)
        assert outcome.status == "complete" and outcome.kind == "rebalance", outcome.message
        got = {t.symbol: (t.target_weight, t.rank) for t in query(root, select(TargetPosition))}
        expected_rows = signals[pd.to_datetime(signals["as_of"]).dt.date == day]
        assert got == {r.symbol: (r.weight, r.rank) for r in expected_rows.itertuples()}

        i = market.session_index(day)
        symbols = set(got) | set(held["symbol"])
        steps = [market.hfq[i + 1, market.symbol_index(s)] / market.hfq[i, market.symbol_index(s)] for s in symbols]
        if np.allclose(steps, 1.0, rtol=0, atol=1e-12):
            first = orders[pd.to_datetime(orders["session"]).dt.date == market.sessions[i + 1]]
            intents = query(root, select(OrderIntent).order_by(OrderIntent.seq))
            assert [(x.symbol, x.side, x.qty) for x in intents] == \
                   [(r.symbol, r.side, int(r.quantity)) for r in first.itertuples()]
            compared_orders += 1
    assert compared_orders >= 1


def test_paper_account_buys_within_cash_and_reruns_reproduce_the_report(tmp_path: Path, loaded, golden) -> None:
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    paper_account(root, day)
    (first,) = run(root, loaded, day)
    assert first.status == "complete" and first.intents > 0
    intents = query(root, select(OrderIntent))
    assert {x.side for x in intents} == {"buy"} and {x.status for x in intents} <= {"pending_approval",
                                                                                     "rejected_by_risk"}
    assert sum(x.est_notional_fen + x.est_fees_fen for x in intents) <= 1_000_000_000
    next_day = loaded[0].sessions[loaded[0].session_index(day) + 1]
    assert intents[0].valid_until == datetime(next_day.year, next_day.month, next_day.day, 15, 0,
                                              tzinfo=SHANGHAI_TZ)
    (skipped,) = run(root, loaded, day)
    assert skipped.status == "skipped"
    (second,) = run(root, loaded, day, rerun=True)
    assert second.status == "complete" and second.run_id.endswith("-r1")
    a, b = Path(first.report_dir), Path(second.report_dir)
    for name in ("intents.csv", "targets.csv"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    assert (a / "report.md").read_text(encoding="utf-8").replace(first.run_id, "RUN") == \
           (b / "report.md").read_text(encoding="utf-8").replace(second.run_id, "RUN")
    runs = {r.run_id: r.status for r in query(root, select(DecisionRun))}
    assert runs == {first.run_id: "superseded", second.run_id: "complete"}


def test_reviewed_decisions_cannot_be_rerun(tmp_path: Path, loaded, golden) -> None:
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    paper_account(root, day)
    run(root, loaded, day)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        intent = session.scalars(select(OrderIntent).where(OrderIntent.status == "pending_approval")).first()
        intent.status = "approved"
        session.add(Approval(intent_id=intent.intent_id, action="approve", actor="test"))
        session.commit()
    (outcome,) = run(root, loaded, day, rerun=True)
    assert outcome.status == "skipped" and "reviewed" in outcome.message


def test_gates_block_and_a_later_complete_run_replaces_the_block(tmp_path: Path, loaded, golden) -> None:
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    paper_account(root, day)
    (blocked,) = run(root, loaded, day, status="partial")
    assert blocked.status == "blocked"
    (again,) = run(root, loaded, day, status="partial")
    assert again.run_id == blocked.run_id  # the same failure is not recorded twice
    (mismatch,) = run(root, loaded, day, version="other")
    assert mismatch.status == "blocked" and "数据版本" in mismatch.message
    assert not query(root, select(OrderIntent))
    events = {e.event_id for e in query(root, select(Event))}
    assert f"{blocked.run_id}-blocked" in events and f"{mismatch.run_id}-blocked" in events
    (complete,) = run(root, loaded, day)
    assert complete.status == "complete"
    statuses = {r.run_id: r.status for r in query(root, select(DecisionRun))}
    assert statuses[blocked.run_id] == statuses[mismatch.run_id] == "superseded"
    (late_failure,) = run(root, loaded, day, status="partial")
    assert late_failure.status == "skipped"  # never replaces a finished decision


def test_manual_account_with_unfilled_approvals_is_blocked_at_the_next_rebalance(tmp_path: Path, loaded,
                                                                                golden) -> None:
    root = make_root(tmp_path)
    days = rebalance_days(golden)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        create_account(session, root, account_id="m", name="m", mode="manual", strategy_config=CONFIG,
                       initial_cash_fen=1_000_000_000, start_date=days[10], actor="test")
        session.commit()
    (first,) = run(root, loaded, days[10])
    assert first.status == "complete"
    with factory() as session:
        intent = session.scalars(select(OrderIntent).where(OrderIntent.status == "pending_approval")).first()
        intent.status = "approved"
        session.commit()
    (second,) = run(root, loaded, days[11])
    assert second.status == "blocked" and "回填" in second.message


def test_monitoring_day_alerts_on_ex_dates_and_expires_old_intents(tmp_path: Path, loaded, golden) -> None:
    market, _, dividends = loaded
    root = make_root(tmp_path)
    days = rebalance_days(golden)
    paper_account(root, days[10])
    run(root, loaded, days[10])
    i = market.session_index(days[10])
    monitor_day = market.sessions[i + 2]  # not a month end
    (outcome,) = run(root, loaded, monitor_day)
    assert outcome.status == "complete" and outcome.kind == "monitor" and outcome.intents == 0
    assert {x.status for x in query(root, select(OrderIntent))} <= {"expired", "rejected_by_risk"}
    ex = dividends[pd.to_datetime(dividends["ex_date"]).dt.date > days[12]].iloc[0]
    ex_day = pd.Timestamp(ex["ex_date"]).date()
    before = market.sessions[market.session_index(ex_day) - 1]
    root2 = make_root(tmp_path / "ex")
    _, factory = open_database(root2 / "app.sqlite")
    with factory() as session:
        create_account(session, root2, account_id="m", name="m", mode="manual", strategy_config=CONFIG,
                       initial_cash_fen=100_000_000, start_date=before, actor="test")
        add_event(session, "m", before, "adjustment", {"old_quantity": 0, "new_quantity": 1000, "cost_fen": 0},
                  event_id="h", actor="test", symbol=str(ex["symbol"]))
        session.commit()
    run(root2, loaded, before)
    titles = [e.title for e in query(root2, select(Event).where(Event.symbol == str(ex["symbol"])))]
    assert any("除权" in t for t in titles)


def test_accounts_are_not_decided_before_their_start_date(tmp_path: Path, loaded, golden) -> None:
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    paper_account(root, day + timedelta(days=1))
    (outcome,) = run(root, loaded, day)
    assert outcome.status == "skipped" and not query(root, select(DecisionRun))
