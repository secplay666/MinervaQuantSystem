"""Manual accounts: suggested ex-rights and ex-dividend adjustments, confirmed by the user."""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("fastapi")

from test_api import add_user, auth, env, login  # noqa: E402,F401  (fixtures)
from test_decision import CONFIG  # noqa: E402


def _plans(root, rows) -> None:
    with duckdb.connect(str(root / "data" / "market.duckdb")) as con:
        con.execute("CREATE TABLE dividends (symbol VARCHAR, ex_date DATE, cash_per_10 DOUBLE, bonus_per_10 DOUBLE, "
                    "transfer_per_10 DOUBLE, plan_profile VARCHAR)")
        con.executemany("INSERT INTO dividends VALUES (?, ?, ?, ?, ?, ?)", rows)


def test_manual_accounts_confirm_suggested_ex_rights_adjustments(env) -> None:
    root, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    admin = auth(login(client, "admin"))
    assert client.post("/api/v1/accounts", headers=admin, json={
        "account_id": "real", "name": "实盘", "mode": "manual", "cash": "100000", "strategy_config": CONFIG,
        "start_date": "2026-09-24"}).status_code == 201
    preview = client.post("/api/v1/accounts/real/holdings/preview", headers=admin, json={
        "as_of": "2026-09-24", "cash": "50000", "text": "600000,1001,9.5\n000001,200"}).json()
    assert client.post("/api/v1/accounts/real/holdings/commit", headers=admin,
                       json={"batch_id": preview["batch_id"], "reason": "初始录入"}).status_code == 200
    _plans(root, [
        ("600000", "2026-09-25", 1.5, 3.0, 2.0, "10送3转2派1.5元"),
        ("000001", "2026-09-24", 2.0, 0.0, 0.0, "10派2元"),  # on the confirmed date: already in the holdings
        ("600036", "2026-09-25", 5.0, 0.0, 0.0, "10派5元"),  # not held
    ])
    pending = client.get("/api/v1/accounts/real/corporate-actions", headers=admin).json()["rows"]
    assert [(r["symbol"], r["old_quantity"], r["new_quantity"], r["cash_fen"]) for r in pending] == \
        [("600000", 1001, 1501, 15015)]  # 1001 x 1.5 = 1501.5 shares, the fraction dropped
    assert pending[0]["name"] == "股票0" and pending[0]["plan"] == "10送3转2派1.5元"

    applied = client.post("/api/v1/accounts/real/corporate-actions", headers=admin, json={
        "event_id": pending[0]["event_id"], "new_quantity": 1501, "cash": "150.10", "reason": "券商到账"})
    assert applied.status_code == 201, applied.text
    account = client.get("/api/v1/accounts/real", headers=admin).json()
    assert {h["symbol"]: h["qty"] for h in account["holdings"]}["600000"] == 1501
    assert account["cash_fen"] == 5_000_000 + 15_010  # the edited amount
    assert client.get("/api/v1/accounts/real/corporate-actions", headers=admin).json()["rows"] == []
    again = client.post("/api/v1/accounts/real/corporate-actions", headers=admin, json={
        "event_id": pending[0]["event_id"], "new_quantity": 1501})
    assert again.status_code == 409

    # Paper accounts adjust themselves; a viewer may look but not confirm.
    add_user(sessions, "viewer", ["viewer"])
    viewer = auth(login(client, "viewer"))
    assert client.get("/api/v1/accounts/real/corporate-actions", headers=viewer).status_code == 200
    assert client.post("/api/v1/accounts/real/corporate-actions", headers=viewer, json={
        "event_id": "x", "new_quantity": 1}).status_code == 403
