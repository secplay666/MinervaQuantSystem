"""Review page support: decision detail extras, batch review, the home page's to-do list."""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import update  # noqa: E402

from quant_system.app.db import open_database, utc_now  # noqa: E402
from quant_system.app.db.models import OrderIntent  # noqa: E402
from quant_system.decision.accounts import create_account  # noqa: E402
from quant_system.decision.job import run_daily  # noqa: E402
from test_api import add_user, auth, env, login  # noqa: E402,F401  (fixtures)
from test_decision import CONFIG, golden, loaded, loaders, rebalance_days  # noqa: E402,F401


def test_batch_review_and_todo(env, loaded, golden) -> None:
    root, settings, client, sessions = env
    add_user(sessions, "rev", ["reviewer"])
    add_user(sessions, "vic", ["viewer"])
    day = rebalance_days(golden)[10]
    _, factory = open_database(settings.db_path)
    with factory() as session:
        create_account(session, root, account_id="paper", name="模拟账户", mode="paper", strategy_config=CONFIG,
                       initial_cash_fen=1_000_000_000, start_date=day, actor="test")
        session.commit()
    (outcome,) = run_daily(root, db_path=settings.db_path, session_date=day, loaders=loaders(loaded, day))
    with factory() as session:
        session.execute(update(OrderIntent).values(valid_until=utc_now() + timedelta(days=1)))
        session.commit()
    reviewer, viewer = auth(login(client, "rev")), auth(login(client, "vic"))

    detail = client.get(f"/api/v1/decisions/{outcome.run_id}", headers=viewer).json()
    assert detail["account_mode"] == "paper" and detail["paper_cutoff"].endswith("09:15:00+08:00")
    intents = [i for i in detail["intents"] if i["status"] == "pending_approval"]
    assert all(i["paper_locked"] is False for i in detail["intents"])
    assert all(i["target_weight"] is not None for i in intents if i["side"] == "buy")

    todo = client.get("/api/v1/todo", headers=viewer).json()
    [entry] = todo["accounts"]
    assert entry["latest"]["run_id"] == outcome.run_id and entry["pending"] == len(intents)
    assert entry["pending_runs"][0]["paper_cutoff"] and entry["pending_runs"][0]["pending"] == len(intents)

    ids = [i["intent_id"] for i in intents]
    assert client.post(f"/api/v1/decisions/{outcome.run_id}/review-batch", headers=viewer,
                       json={"intent_ids": ids[:2], "action": "approve"}).status_code == 403
    result = client.post(f"/api/v1/decisions/{outcome.run_id}/review-batch", headers=reviewer,
                         json={"intent_ids": [*ids[:3], "nope", ids[0]], "action": "approve"}).json()
    assert result["done"] == 3 and [f["intent_id"] for f in result["failed"]] == ["nope"]
    assert client.post(f"/api/v1/decisions/{outcome.run_id}/review-batch", headers=reviewer,
                       json={"intent_ids": ids[3:5], "action": "reject"}).status_code == 400
    rejected = client.post(f"/api/v1/decisions/{outcome.run_id}/review-batch", headers=reviewer,
                           json={"intent_ids": ids[3:5], "action": "reject", "reason": "流动性差"}).json()
    assert rejected["done"] == 2
    again = client.post(f"/api/v1/decisions/{outcome.run_id}/review-batch", headers=reviewer,
                        json={"intent_ids": ids[:1], "action": "approve"}).json()
    assert again["done"] == 0 and "不能批准" in again["failed"][0]["message"]

    entry = client.get("/api/v1/todo", headers=viewer).json()["accounts"][0]
    assert entry["pending"] == len(intents) - 5

    # A later blocked day does not hide the list that is still open.
    next_day = loaded[0].sessions[loaded[0].session_index(day) + 1]
    (blocked,) = run_daily(root, db_path=settings.db_path, session_date=next_day,
                           loaders=loaders(loaded, next_day, status="failed"))
    assert blocked.status == "blocked"
    entry = client.get("/api/v1/todo", headers=viewer).json()["accounts"][0]
    assert entry["latest"]["status"] == "blocked" and entry["latest"]["failed_gate"]["gate"] == "G1"
    assert entry["pending"] == len(intents) - 5 and entry["pending_runs"][0]["run_id"] == outcome.run_id
