"""Stage-4 review hardening (self-review 2026-09-28): paper locks and
reversals, modify re-checks, entry dates, fees, stale previews, holdings
paste, client IP and re-authentication."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database, utc_now  # noqa: E402
from quant_system.app.db.models import Account, DecisionRun, OrderIntent, RiskCheck  # noqa: E402
from quant_system.app.services import account_costs  # noqa: E402
from quant_system.decision import review  # noqa: E402
from quant_system.decision.job import run_daily  # noqa: E402
from quant_system.ledger import Ledger, LedgerInvariantError, TradeFilled  # noqa: E402
from test_api import PASSWORD, add_user, auth, env, login  # noqa: E402,F401  (fixtures)
from test_decision import CONFIG, golden, loaded, loaders, make_root, paper_account, rebalance_days  # noqa: E402,F401
from test_paper import _manual_intent, at  # noqa: E402

OP = review.Operator("tester")


def _decided(tmp_path: Path, loaded, golden):
    """A paper account with a complete rebalance decision; the review window reopened."""
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    paper_account(root, day)
    (outcome,) = run_daily(root, db_path=root / "app.sqlite", session_date=day, loaders=loaders(loaded, day))
    assert outcome.status == "complete"
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        for intent in session.scalars(select(OrderIntent)):
            intent.valid_until = utc_now() + timedelta(days=1)
        session.commit()
    return root, factory, outcome.run_id


def _modify(session, root: Path, intent: OrderIntent, qty: int):
    account = session.get(Account, intent.account_id)
    fees, rules = account_costs(root, account)
    exchange, board = ("SSE", "SSE_MAIN") if intent.symbol.startswith("6") else ("SZSE", "SZSE_MAIN")
    return review.modify(session, intent.intent_id, qty, OP, utc_now(), "测试", rules.lot_rule(board), fees, exchange)


def test_modify_reruns_the_quantity_checks(tmp_path: Path, loaded, golden) -> None:
    root, factory, run_id = _decided(tmp_path, loaded, golden)
    with factory() as session:
        run = session.get(DecisionRun, run_id)
        assert run.summary["limits"]["volume_cap"] and run.summary["limits"]["max_weight"]
        buy = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id, OrderIntent.side == "buy")
                              .order_by(OrderIntent.seq)).first()
        with pytest.raises(review.ReviewError) as refused:  # 50x the size: R5 and R6 reject
            _modify(session, root, buy, buy.qty * 50)
        assert refused.value.code == "risk_reject" and "R5" in str(refused.value)
        session.rollback()
        smaller = _modify(session, root, buy, 100)
        session.commit()
        assert smaller.status == "modified" and smaller.qty == 100 and smaller.risk in ("pass", "warn")
        rules = set(session.scalars(select(RiskCheck.rule_id).where(RiskCheck.intent_id == buy.intent_id)))
        assert "R5" not in rules

        # Decisions made before limits were recorded may only shrink.
        other = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id, OrderIntent.side == "buy",
                                                          OrderIntent.intent_id != buy.intent_id)).first()
        run.summary = {k: v for k, v in run.summary.items() if k != "limits"}
        session.commit()
        with pytest.raises(review.ReviewError) as refused:
            _modify(session, root, other, other.qty + 1000)
        assert refused.value.code == "no_limits"
        session.rollback()
        assert _modify(session, root, other, 100).qty == 100


def test_paper_intents_lock_between_the_cutoff_and_the_booking(tmp_path: Path, loaded) -> None:
    market = loaded[0]
    day, execute_on = market.sessions[300], market.sessions[301]
    root = make_root(tmp_path)
    _manual_intent(root, day, execute_on, str(market.symbols[0]), 100)  # approved at 20:00 on `day`
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        intent = session.get(OrderIntent, "i-1")
        intent.valid_until = at(execute_on, 15)
        assert not review.paper_locked(session, intent, at(execute_on, 9, 0))  # before the call auction
        assert review.paper_locked(session, intent, at(execute_on, 9, 20))
        assert review.paper_locked(session, intent, at(execute_on, 18))
        with pytest.raises(review.ReviewError) as refused:
            review.reject(session, "i-1", OP, at(execute_on, 14), "看到开盘后反悔")
        assert refused.value.code == "paper_locked"
        session.get(Account, "paper").paper_through = execute_on  # the paper job booked the session
        assert not review.paper_locked(session, intent, at(execute_on, 20))
        session.get(Account, "paper").paper_through = None
        intent.status = "pending_approval"  # never approved: nothing was simulated
        assert not review.paper_locked(session, intent, at(execute_on, 14))


def test_paper_ledgers_cannot_be_reversed_and_closed_intents_stay_closed(tmp_path: Path, loaded) -> None:
    market = loaded[0]
    root = make_root(tmp_path)
    _manual_intent(root, market.sessions[300], market.sessions[301], str(market.symbols[0]), 100)
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        account = session.get(Account, "paper")
        event = session.scalars(select(review.PositionEvent).where(review.PositionEvent.account_id == "paper")).first()
        with pytest.raises(review.ReviewError) as refused:
            review.reverse(session, account, event.event_id, OP, "试图冲销")
        assert refused.value.code == "paper_account"
        intent = session.get(OrderIntent, "i-1")
        intent.execution = "unfilled"
        review.refresh_intent_fill(session, "i-1")
        assert intent.execution == "unfilled"


def test_manual_entries_respect_the_confirmed_holdings_and_today(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    admin = auth(login(client, "admin"))
    assert client.post("/api/v1/accounts", headers=admin, json={
        "account_id": "real", "name": "实盘", "mode": "manual", "cash": "100000", "strategy_config": CONFIG,
        "start_date": "2026-09-23"}).status_code == 201

    def preview(as_of: str, text: str, cash: str = "50000") -> dict:
        return client.post("/api/v1/accounts/real/holdings/preview", headers=admin,
                           json={"as_of": as_of, "cash": cash, "text": text}).json()

    def commit(batch: dict):
        return client.post("/api/v1/accounts/real/holdings/commit", headers=admin,
                           json={"batch_id": batch["batch_id"], "reason": "录入"})

    def fill(trade_date: str, **extra):
        body = {"trade_date": trade_date, "symbol": "600000", "side": "sell", "qty": 100, "price": "10", **extra}
        return client.post("/api/v1/accounts/real/fills", headers=admin, json=body)

    assert "晚于今天" in " ".join(preview("2099-01-02", "600000\t1,000\t10.50")["errors"])
    assert commit(preview("2026-09-24", "600000\t1,000\t10.50")).status_code == 200  # thousands separator
    holdings = client.get("/api/v1/accounts/real", headers=admin).json()["holdings"]
    assert {h["symbol"]: h["qty"] for h in holdings} == {"600000": 1000}

    same_day = fill("2026-09-24")  # already inside the confirmed holdings
    assert same_day.status_code == 400 and same_day.json()["detail"]["code"] == "before_confirmed"
    assert fill("2099-01-02").json()["detail"]["code"] == "future_date"
    negative = fill("2026-09-25", commission="-100000")
    assert negative.status_code == 400 and negative.json()["detail"]["code"] == "invalid_fill"

    # A preview is stale after any new ledger event, not only one touching its diff.
    stale = preview("2026-09-25", "600000,1000,10.5\n600036,100,40")
    assert [d["symbol"] for d in stale["diff"]] == ["600036"]
    assert fill("2026-09-25").status_code == 201
    refused = commit(stale)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "stale_preview"

    confirmed = client.get("/api/v1/accounts/real/events", headers=admin).json()
    old = next(e["event_id"] for e in confirmed if e["trade_date"] == "2026-09-24")
    reversal = client.post(f"/api/v1/position-events/{old}/reverse", headers=admin, json={"reason": "改历史"})
    assert reversal.status_code == 400 and reversal.json()["detail"]["code"] == "before_confirmed"


def test_ledger_rejects_negative_fees() -> None:
    ledger = Ledger(cash_fen=1_000_000)
    with pytest.raises(LedgerInvariantError, match="negative fee"):
        ledger.apply(TradeFilled("t1", date(2026, 9, 25), "600000", "buy", 100, 1000, 100_000, -900_000, 0, 0))


def test_parse_holdings_text_keeps_thousands_separators() -> None:
    rows, errors = review.parse_holdings_text("代码\t数量\t成本\n600000\t1,000\t1,234.5\n000001,200,9.8\n")
    assert not errors
    assert rows == [{"symbol": "600000", "qty": 1000, "cost_price": 1234.5},
                    {"symbol": "000001", "qty": 200, "cost_price": 9.8}]


def test_forwarded_for_is_not_trusted(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "erin", ["viewer"])
    for k in range(20):  # a different X-Forwarded-For on every guess must not reset the throttle
        response = client.post("/api/v1/auth/login", headers={"X-Forwarded-For": f"10.0.0.{k}"},
                               json={"username": f"guess{k}", "password": "x"})
        assert response.status_code == 401
    assert client.post("/api/v1/auth/login", headers={"X-Forwarded-For": "10.9.9.9"},
                       json={"username": "erin", "password": PASSWORD}).status_code == 429


def test_reauthentication_counts_towards_lockout_and_totp_needs_the_password(env) -> None:
    import pyotp

    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    add_user(sessions, "dave", ["viewer"])
    dave = auth(login(client, "dave"))
    secret = client.post("/api/v1/auth/totp/setup", headers=dave).json()["secret"]
    code = pyotp.TOTP(secret).now()
    wrong = client.post("/api/v1/auth/totp/enable", headers=dave,
                        json={"secret": secret, "code": code, "password": "nope"})
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "wrong_password"
    assert client.post("/api/v1/auth/totp/enable", headers=dave,
                       json={"secret": secret, "code": code, "password": PASSWORD}).status_code == 200
    again = client.post("/api/v1/auth/totp/enable", headers=dave,
                        json={"secret": secret, "code": code, "password": PASSWORD})
    assert again.status_code == 409

    for _ in range(5):  # the successful check above reset the count: five wrong guesses lock
        client.post("/api/v1/auth/password", headers=dave, json={"old_password": "guess", "new_password": "x" * 12})
    locked = client.post("/api/v1/auth/password", headers=dave,
                         json={"old_password": PASSWORD, "new_password": "Another-pass-789"})
    assert locked.status_code == 401 and locked.json()["detail"]["code"] == "locked"  # the token stops working

    admin = auth(login(client, "admin"))
    users = client.get("/api/v1/users", headers=admin).json()
    dave_id = next(u["id"] for u in users if u["username"] == "dave")
    assert client.post(f"/api/v1/users/{dave_id}/reset-password", headers=admin).status_code == 200
    with sessions() as session:
        from quant_system.app.db.models import User

        assert session.get(User, dave_id).totp_secret is None


def test_paper_lock_follows_the_exchange_calendar(tmp_path: Path, loaded) -> None:
    """A retrying intent booked through the last session before a holiday is
    not locked during the holiday, only from the next session's cutoff."""
    market = loaded[0]
    day, execute_on = market.sessions[300], market.sessions[301]
    root = make_root(tmp_path)
    _manual_intent(root, day, execute_on, str(market.symbols[0]), 100)
    holiday = execute_on + timedelta(days=1)
    while holiday.weekday() >= 5:
        holiday += timedelta(days=1)
    reopen = holiday + timedelta(days=7)
    sessions = [d for d in market.sessions if d <= execute_on] + [reopen]  # a week off after execute_on
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        intent = session.get(OrderIntent, "i-1")
        session.get(Account, "paper").paper_through = execute_on  # tried on execute_on, still open
        assert not review.paper_locked(session, intent, at(holiday, 10), sessions)
        assert review.paper_locked(session, intent, at(holiday, 10))  # weekday fallback: conservative
        assert not review.paper_locked(session, intent, at(reopen, 9, 0), sessions)
        assert review.paper_locked(session, intent, at(reopen, 9, 30), sessions)


def test_reductions_are_never_refused_by_the_cash_check(tmp_path: Path, loaded, golden) -> None:
    root, factory, run_id = _decided(tmp_path, loaded, golden)
    with factory() as session:
        run = session.get(DecisionRun, run_id)
        run.cash_fen = 1  # as if the sells that funded the buys had been rejected
        session.commit()
        buy = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id, OrderIntent.side == "buy")
                              .order_by(OrderIntent.seq)).first()
        with pytest.raises(review.ReviewError):
            _modify(session, root, buy, buy.qty + 100)
        session.rollback()
        smaller = _modify(session, root, buy, 100)
        session.commit()
        checks = {c.rule_id: c.decision for c in session.scalars(
            select(RiskCheck).where(RiskCheck.intent_id == buy.intent_id))}
        assert smaller.status == "modified" and checks.get("R6") == "warn"


def test_reversal_dates_are_checked(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    admin = auth(login(client, "admin"))
    client.post("/api/v1/accounts", headers=admin, json={
        "account_id": "real", "name": "实盘", "mode": "manual", "cash": "100000", "strategy_config": CONFIG,
        "start_date": "2026-09-23"})
    assert client.post("/api/v1/accounts/real/fills", headers=admin, json={
        "trade_date": "2026-09-25", "symbol": "600000", "side": "buy", "qty": 100, "price": "10"}).status_code == 201
    event = next(e["event_id"] for e in client.get("/api/v1/accounts/real/events", headers=admin).json()
                 if e["kind"] == "fill")
    future = client.post(f"/api/v1/position-events/{event}/reverse", headers=admin,
                         json={"reason": "x", "trade_date": "2099-01-02"})
    assert future.json()["detail"]["code"] == "future_date"
    earlier = client.post(f"/api/v1/position-events/{event}/reverse", headers=admin,
                          json={"reason": "x", "trade_date": "2026-09-24"})
    assert earlier.json()["detail"]["code"] == "invalid_date"


def test_space_separated_holdings_keep_thousands_separators() -> None:
    rows, errors = review.parse_holdings_text("600000 1,000 10.50\n600036, 200, 40\n000001，300\n")
    assert not errors
    assert [(r["symbol"], r["qty"], r["cost_price"]) for r in rows] == [
        ("600000", 1000, 10.5), ("600036", 200, 40.0), ("000001", 300, None)]


def test_industry_limits_are_recorded_and_rechecked_on_modify(tmp_path: Path, loaded, golden) -> None:
    from quant_system.decision.intents import IndustryLimits, industry_check

    limits = IndustryLimits({"600000": "银行"}, {"银行": 0.10}, 0.05)
    assert industry_check("银行", 0.16, limits) is None  # within 1.25x of the deviation
    assert industry_check("银行", 0.17, limits).decision == "warn"
    assert industry_check("银行", 0.21, limits).decision == "reject"

    root, factory, run_id = _decided(tmp_path, loaded, golden)
    with factory() as session:
        run = session.get(DecisionRun, run_id)
        industry = run.summary["limits"]["industry"]
        assert sum(industry["bench"].values()) == pytest.approx(1.0) and industry["delta"] >= 0.05
        # The target respects the construction's bounds, so a normal decision has no industry reject.
        assert not any("行业" in (c.message or "") and c.decision == "reject"
                       for c in session.scalars(select(RiskCheck).where(RiskCheck.run_id == run_id)))
        buy = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id, OrderIntent.side == "buy")
                              .order_by(OrderIntent.seq)).first()
        label = industry["of"][buy.symbol]
        # Tighten the recorded bounds so this industry is already far over its limit.
        tight = {**industry, "bench": {**industry["bench"], label: 0.0}, "delta": 0.001}
        run.summary = {**run.summary, "limits": {**run.summary["limits"], "industry": tight}}
        session.commit()
        with pytest.raises(review.ReviewError) as refused:
            _modify(session, root, buy, buy.qty + 100)
        assert refused.value.code == "risk_reject" and "行业" in str(refused.value)
        session.rollback()
        smaller = _modify(session, root, buy, 100)  # a reduction is never refused; the check stays as a warning
        session.commit()
        messages = list(session.scalars(select(RiskCheck.message).where(RiskCheck.intent_id == buy.intent_id)))
        assert smaller.status == "modified" and any("行业" in m and "仅提示" in m for m in messages)
