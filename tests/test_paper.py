"""Stage-4 P6: paper execution (ADR-008 §2, §6, §12)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database  # noqa: E402
from quant_system.app.db.models import (  # noqa: E402
    Account,
    AccountSnapshot,
    Approval,
    DecisionRun,
    Event,
    Fill,
    OrderIntent,
    PositionEvent,
)
from quant_system.backtest.config import BacktestConfig  # noqa: E402
from quant_system.backtest.runner import run_backtest  # noqa: E402
from quant_system.data_platform.sessions import SHANGHAI_TZ  # noqa: E402
from quant_system.decision.accounts import create_account, replay  # noqa: E402
from quant_system.decision.paper import run_paper  # noqa: E402
from quant_system.domain.rules import MarketRules  # noqa: E402
from quant_system.strategy.registry import StrategyContext  # noqa: E402
from test_decision import CONFIG, ROOT, golden, loaded, make_root, paper_account, rebalance_days, run  # noqa: E402,F401

RULES = MarketRules.load(ROOT / "configs" / "market_rules" / "cn_a_share.json")


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI_TZ)


def approve_all(root: Path, when: datetime) -> int:
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        intents = session.scalars(select(OrderIntent).where(OrderIntent.status == "pending_approval")).all()
        for intent in intents:
            intent.status = "approved"
            session.add(Approval(intent_id=intent.intent_id, action="approve", actor="test", at=when))
        session.commit()
        return len(intents)


def config_for(start: date, end: date) -> BacktestConfig:
    import json

    payload = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    payload["run"] = {**payload["run"], "start": start.isoformat(), "end": end.isoformat()}
    return BacktestConfig.from_payload(payload, ROOT)


def test_paper_fills_reproduce_the_backtest_on_a_clean_rebalance(tmp_path: Path, loaded, golden) -> None:
    """ADR-008 §12: every intent approved before the cutoff -> the paper fills
    and the NAV equal the backtest's first-day fills and NAV."""
    market, research, _ = loaded
    compared = 0
    for k, day in enumerate(rebalance_days(golden)[6:14]):
        i = market.session_index(day)
        following = market.sessions[i + 1]
        root = make_root(tmp_path / f"r{k}")
        paper_account(root, day)
        (decision,) = run(root, loaded, day)
        assert decision.status == "complete"
        intents = [x.symbol for x in root_intents(root)]
        steps = [market.hfq[i + 1, market.symbol_index(s)] / market.hfq[i, market.symbol_index(s)] for s in intents]
        if not np.allclose(steps, 1.0, rtol=0, atol=1e-12):
            continue  # the backtest sizes at T+1's ex-rights reference price: not comparable
        approve_all(root, at(day, 20))
        (monitor,) = run(root, loaded, following)
        assert monitor.status == "complete" and monitor.kind == "monitor"
        result, _, _ = run_backtest(config_for(day, following), market, StrategyContext(ROOT, market, lambda: research))
        expected = result.fills[pd.to_datetime(result.fills["session"]).dt.date == following]
        _, factory = open_database(root / "app.sqlite")
        with factory() as session:
            fills = session.scalars(select(Fill).order_by(Fill.fill_id)).all()
            got = sorted((f.symbol, f.side, f.qty, f.price_fen, f.commission_fen + f.stamp_duty_fen + f.transfer_fee_fen)
                         for f in fills)
            nav = session.get(AccountSnapshot, ("paper", following)).nav_fen
        want = sorted((r.symbol, r.side, int(r.quantity), int(r.price_fen),
                       int(r.commission_fen + r.stamp_duty_fen + r.transfer_fee_fen)) for r in expected.itertuples())
        assert got == want
        assert nav == int(result.nav[pd.to_datetime(result.nav["session"]).dt.date == following]["nav_fen"].iloc[0])
        compared += 1
        if compared == 2:
            break
    assert compared >= 1


def root_intents(root: Path) -> list[OrderIntent]:
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        return list(session.scalars(select(OrderIntent)))


def test_late_approvals_trade_from_the_next_session_and_reruns_are_noops(tmp_path: Path, loaded, golden) -> None:
    market = loaded[0]
    day = rebalance_days(golden)[10]
    i = market.session_index(day)
    first, second = market.sessions[i + 1], market.sessions[i + 2]
    root = make_root(tmp_path)
    paper_account(root, day)
    run(root, loaded, day)
    approve_all(root, at(first, 10))  # after the 09:15 cutoff of the first execution session
    run(root, loaded, first)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        assert not session.scalars(select(Fill)).all()
    run(root, loaded, second)
    with factory() as session:
        fills = session.scalars(select(Fill)).all()
        assert fills and {f.trade_date for f in fills} == {second}
        events = len(session.scalars(select(PositionEvent)).all())
        account = session.get(Account, "paper")
        assert account.paper_through == second
        config = config_for(day, second)
        run_paper(session, account, market, RULES, config, through=second)  # already processed
        session.commit()
        assert len(session.scalars(select(PositionEvent)).all()) == events


def _manual_intent(root: Path, account_day: date, execute_on: date, symbol: str, qty: int, side: str = "buy") -> None:
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        create_account(session, root, account_id="paper", name="模拟", mode="paper", strategy_config=CONFIG,
                       initial_cash_fen=100_000_000, start_date=account_day, actor="test")
        session.add(DecisionRun(run_id="dr-x", account_id="paper", trade_date=account_day, next_session=execute_on,
                                kind="forced", status="complete", gates=[], created_by="test"))
        session.flush()
        session.add(OrderIntent(intent_id="i-1", run_id="dr-x", account_id="paper", trade_date=account_day,
                                execute_on=execute_on, seq=1, symbol=symbol, side=side, qty=qty, proposed_qty=qty,
                                ref_price_fen=1000, est_notional_fen=0, est_fees_fen=0, reason="test", risk="pass",
                                status="approved", valid_until=at(execute_on, 15)))
        session.flush()
        session.add(Approval(intent_id="i-1", action="approve", actor="test", at=at(account_day, 20)))
        session.commit()


def test_buys_of_a_suspended_stock_retry_then_give_up(tmp_path: Path, loaded) -> None:
    market = loaded[0]
    config = config_for(market.sessions[300], market.sessions[-1])
    found = None
    for j in range(len(market.symbols)):
        bars = market.has_bar[:, j]
        for i in range(300, len(market.sessions) - 10):
            if bars[i - 1] and not bars[i:i + config.buy_retry_sessions + 1].any():
                found = (i, j)
                break
        if found:
            break
    assert found, "fixture has a multi-session suspension"
    i, j = found
    root = make_root(tmp_path)
    _manual_intent(root, market.sessions[i - 1], market.sessions[i], str(market.symbols[j]), 100)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        account = session.get(Account, "paper")
        run_paper(session, account, market, RULES, config, through=market.sessions[i + config.buy_retry_sessions])
        session.commit()
        intent = session.get(OrderIntent, "i-1")
        assert intent.execution == "unfilled" and intent.filled_qty == 0 and "停牌" in intent.execution_note
        assert not session.scalars(select(Fill)).all()


def test_holdings_follow_corporate_actions(tmp_path: Path, loaded) -> None:
    market = loaded[0]
    config = config_for(market.sessions[300], market.sessions[-1])
    found = None
    for j in range(len(market.symbols)):
        for i in range(305, len(market.sessions) - 2):
            step = market.hfq[i, j] / market.hfq[i - 1, j]
            if np.isfinite(step) and abs(step - 1) > 0.05 and market.has_bar[i - 3:i + 1, j].all():
                found = (i, j, step)
                break
        if found:
            break
    assert found, "fixture has an ex-rights step"
    i, j, step = found
    symbol = str(market.symbols[j])
    root = make_root(tmp_path)
    _manual_intent(root, market.sessions[i - 4], market.sessions[i - 3], symbol, 1000)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        account = session.get(Account, "paper")
        run_paper(session, account, market, RULES, config, through=market.sessions[i])
        session.commit()
        held = replay(session, "paper").quantity(symbol)
        filled = session.get(OrderIntent, "i-1").filled_qty
        assert filled > 0
        assert held == int(np.floor(filled * step + 1e-9))
        kinds = [e.kind for e in session.scalars(select(PositionEvent).order_by(PositionEvent.seq))]
        assert "corporate_action" in kinds
        snapshots = session.scalars(select(AccountSnapshot).where(AccountSnapshot.account_id == "paper")).all()
        assert len(snapshots) == 5  # one per simulated session, the start date included


def test_the_paper_outcome_survives_a_failing_decision(tmp_path: Path, loaded, golden, monkeypatch) -> None:
    import quant_system.decision.job as job

    market = loaded[0]
    day = rebalance_days(golden)[10]
    first = market.sessions[market.session_index(day) + 1]
    root = make_root(tmp_path)
    paper_account(root, day)
    run(root, loaded, day)
    approve_all(root, at(first, 8))

    def broken(*args, **kwargs):
        raise RuntimeError("monitor broke")

    monkeypatch.setattr(job, "monitor_holdings", broken)
    (outcome,) = run(root, loaded, first)
    assert outcome.status == "failed"
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        assert session.scalars(select(Fill)).all()
        titles = [e.title for e in session.scalars(select(Event).where(Event.category == "account"))]
        assert any(t.startswith("模拟成交") for t in titles)
