"""Stage-4 P2: API, authentication, permissions and review (ADR-010)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import duckdb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, update  # noqa: E402

from quant_system.app.db import open_database, utc_now  # noqa: E402
from quant_system.app.db.models import AuditLog, OrderIntent, User, UserRole  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.rbac import sync_roles  # noqa: E402
from quant_system.app.security import hash_password  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from test_decision import CONFIG, golden, loaded, loaders, make_root, rebalance_days  # noqa: E402,F401  (fixtures)
from quant_system.decision.job import run_daily  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Initial-pass-123"
NEW_PASSWORD = "Better-pass-456"


def tiny_market(root: Path, symbols: list[str]) -> None:
    path = root / "data" / "market.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE security_master (symbol VARCHAR, name VARCHAR, exchange VARCHAR, board VARCHAR, "
                    "list_date DATE, delist_date DATE, status VARCHAR)")
        con.execute("CREATE TABLE daily_bars (symbol VARCHAR, trade_date DATE, close DOUBLE)")
        con.execute("CREATE TABLE trading_calendar (trade_date DATE)")
        con.execute("INSERT INTO trading_calendar VALUES (DATE '2026-09-24')")
        for k, symbol in enumerate(symbols):
            exchange, board = ("SSE", "SSE_MAIN") if symbol.startswith("6") else ("SZSE", "SZSE_MAIN")
            con.execute("INSERT INTO security_master VALUES (?, ?, ?, ?, DATE '2010-01-04', NULL, 'listed')",
                        [symbol, f"股票{k}", exchange, board])
            con.execute("INSERT INTO daily_bars VALUES (?, DATE '2026-09-24', ?)", [symbol, 10.0 + k])


@pytest.fixture
def env(tmp_path: Path):
    root = make_root(tmp_path / "root")
    tiny_market(root, ["600000", "600036", "000001"])
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    app = create_app(settings)
    client = TestClient(app)
    _, sessions = open_database(settings.db_path)
    return root, settings, client, sessions


def add_user(sessions, username: str, roles: list[str], must_change: bool = False) -> None:
    with sessions() as session:
        sync_roles(session)
        user = User(username=username, display_name=username, password_hash=hash_password(PASSWORD),
                    must_change_password=must_change)
        session.add(user)
        session.flush()
        session.add_all(UserRole(user_id=user.id, role_code=r) for r in roles)
        session.commit()


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_first_login_requires_a_password_change(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"], must_change=True)
    tokens = login(client, "admin")
    assert tokens["user"]["must_change_password"]
    blocked = client.get("/api/v1/accounts", headers=auth(tokens))
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "password_change_required"
    weak = client.post("/api/v1/auth/password", headers=auth(tokens),
                       json={"old_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 400
    changed = client.post("/api/v1/auth/password", headers=auth(tokens),
                          json={"old_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert changed.status_code == 200
    fresh = changed.json()
    assert client.get("/api/v1/accounts", headers=auth(fresh)).status_code == 200
    assert "user:manage" in client.get("/api/v1/auth/me", headers=auth(fresh)).json()["permissions"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_lockout_after_repeated_failures_and_unlock(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    add_user(sessions, "bob", ["viewer"])
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong-1"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "bob", "password": PASSWORD}).status_code == 423
    admin = login(client, "admin")
    bob_id = next(u["id"] for u in client.get("/api/v1/users", headers=auth(admin)).json() if u["username"] == "bob")
    assert client.post(f"/api/v1/users/{bob_id}/unlock", headers=auth(admin)).status_code == 200
    login(client, "bob")
    assert client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401


def test_login_failures_are_throttled_per_client_ip(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "erin", ["viewer"])
    for k in range(20):  # spraying different usernames from one address
        assert client.post("/api/v1/auth/login", json={"username": f"guess{k}", "password": "x"}).status_code == 401
    throttled = client.post("/api/v1/auth/login", json={"username": "erin", "password": PASSWORD})
    assert throttled.status_code == 429 and throttled.json()["detail"]["code"] == "too_many_attempts"


def test_refresh_tokens_rotate_and_reuse_ends_all_sessions(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "carol", ["viewer"])
    first = login(client, "carol")
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    assert second["refresh_token"] != first["refresh_token"]
    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401 and reused.json()["detail"]["code"] == "token_reused"
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_permissions_by_role(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "viewer", ["viewer"])
    add_user(sessions, "reviewer", ["reviewer"])
    viewer, reviewer = login(client, "viewer"), login(client, "reviewer")
    assert client.get("/api/v1/decisions", headers=auth(viewer)).status_code == 200
    assert client.post("/api/v1/intents/x/approve", headers=auth(viewer), json={}).status_code == 403
    assert client.post("/api/v1/intents/x/approve", headers=auth(reviewer), json={}).status_code == 404
    assert client.get("/api/v1/users", headers=auth(reviewer)).status_code == 403
    assert client.post("/api/v1/intents/x/override", headers=auth(reviewer), json={"reason": "r"}).status_code == 403
    assert client.get("/api/v1/accounts").status_code == 401


def test_user_administration_guards_against_lockout(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    admin = login(client, "admin")
    created = client.post("/api/v1/users", headers=auth(admin),
                          json={"username": "dave", "display_name": "Dave", "roles": ["reviewer"]})
    assert created.status_code == 201 and created.json()["temporary_password"]
    assert login(client, "dave", created.json()["temporary_password"])["user"]["must_change_password"]
    me = client.get("/api/v1/auth/me", headers=auth(admin)).json()
    assert client.patch(f"/api/v1/users/{me['id']}", headers=auth(admin), json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/v1/users/{me['id']}", headers=auth(admin),
                        json={"roles": ["viewer"]}).status_code == 400
    role = client.post("/api/v1/roles", headers=auth(admin),
                       json={"code": "watcher", "name": "看盘", "permissions": ["market:view"]})
    assert role.status_code == 201
    assert client.put("/api/v1/roles/watcher/permissions", headers=auth(admin),
                      json={"permissions": ["market:view", "event:view"]}).status_code == 200
    assert client.put("/api/v1/roles/admin/permissions", headers=auth(admin), json={"permissions": []}).status_code == 400
    actions = [row["action"] for row in client.get("/api/v1/audit", headers=auth(admin)).json()]
    assert {"user.create", "role.create", "role.permissions"} <= set(actions)


def test_review_workflow_through_the_api(env, loaded, golden) -> None:
    root, settings, client, sessions = env
    add_user(sessions, "reviewer", ["reviewer"])
    add_user(sessions, "admin", ["admin"])
    day = rebalance_days(golden)[10]
    _, factory = open_database(settings.db_path)
    from quant_system.decision.accounts import create_account

    with factory() as session:
        create_account(session, root, account_id="paper", name="模拟账户", mode="paper", strategy_config=CONFIG,
                       initial_cash_fen=1_000_000_000, start_date=day, actor="test")
        session.commit()
    (outcome,) = run_daily(root, db_path=settings.db_path, session_date=day, loaders=loaders(loaded, day))
    assert outcome.status == "complete"
    with factory() as session:  # the fixture session is in the past: reopen the review window
        session.execute(update(OrderIntent).values(valid_until=utc_now() + timedelta(days=1)))
        session.commit()
    reviewer = login(client, "reviewer")
    runs = client.get("/api/v1/decisions", headers=auth(reviewer)).json()
    assert runs[0]["run_id"] == outcome.run_id
    detail = client.get(f"/api/v1/decisions/{outcome.run_id}", headers=auth(reviewer)).json()
    intents = [i for i in detail["intents"] if i["status"] == "pending_approval"]
    first, second, third = intents[0]["intent_id"], intents[1]["intent_id"], intents[2]["intent_id"]
    assert client.post(f"/api/v1/intents/{first}/approve", headers=auth(reviewer), json={}).json()["status"] == "approved"
    assert client.post(f"/api/v1/intents/{first}/approve", headers=auth(reviewer), json={}).status_code == 409
    bad = client.post(f"/api/v1/intents/{second}/modify", headers=auth(reviewer), json={"qty": 150, "reason": "试"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_qty"
    good = client.post(f"/api/v1/intents/{second}/modify", headers=auth(reviewer), json={"qty": 200, "reason": "减仓"})
    assert good.status_code == 200 and good.json()["qty"] == 200 and good.json()["status"] == "modified"
    assert client.post(f"/api/v1/intents/{third}/reject", headers=auth(reviewer), json={}).status_code == 400
    assert client.post(f"/api/v1/intents/{third}/reject", headers=auth(reviewer),
                       json={"reason": "流动性差"}).json()["status"] == "rejected"
    approved = client.post(f"/api/v1/decisions/{outcome.run_id}/approve-all", headers=auth(reviewer),
                           json={"include_warnings": True}).json()["approved"]
    assert approved == len(intents) - 3
    csv = client.get(f"/api/v1/decisions/{outcome.run_id}/export.csv", headers=auth(reviewer))
    assert csv.text.startswith("\ufeff序号") and csv.text.count("\n") == len(detail["intents"]) + 1
    with factory() as session:
        audited = set(session.scalars(select(AuditLog.action)))
    assert {"intent.approve", "intent.modify", "intent.reject", "decision.run"} <= audited
    events = client.get("/api/v1/events", headers=auth(reviewer)).json()
    assert events["unread"] >= 1
    client.post(f"/api/v1/events/{events['items'][0]['event_id']}/read", headers=auth(reviewer))
    assert client.get("/api/v1/events", headers=auth(reviewer)).json()["unread"] == events["unread"] - 1


def test_manual_holdings_entry_fills_and_reversal(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "admin", ["admin"])
    admin = login(client, "admin")
    created = client.post("/api/v1/accounts", headers=auth(admin),
                          json={"account_id": "real", "name": "实盘", "mode": "manual", "cash": "100000",
                                "strategy_config": CONFIG, "start_date": "2026-09-24"})
    assert created.status_code == 201
    preview = client.post("/api/v1/accounts/real/holdings/preview", headers=auth(admin), json={
        "as_of": "2026-09-24", "cash": "50000", "text": "代码,数量,成本价\n600000,1000,9.5\n999999,100\n"}).json()
    assert preview["errors"]
    refused = client.post("/api/v1/accounts/real/holdings/commit", headers=auth(admin),
                          json={"batch_id": preview["batch_id"], "reason": "初始录入"})
    assert refused.status_code == 400
    preview = client.post("/api/v1/accounts/real/holdings/preview", headers=auth(admin), json={
        "as_of": "2026-09-24", "cash": "50000", "text": "600000.SH\t1000\t9.5\nsz000001 200"}).json()
    assert not preview["errors"] and {d["symbol"] for d in preview["diff"]} == {"600000", "000001"}
    assert client.post("/api/v1/accounts/real/holdings/commit", headers=auth(admin),
                       json={"batch_id": preview["batch_id"], "reason": "初始录入"}).status_code == 200
    account = client.get("/api/v1/accounts/real", headers=auth(admin)).json()
    assert account["cash_fen"] == 5_000_000 and {h["symbol"]: h["qty"] for h in account["holdings"]} == \
        {"600000": 1000, "000001": 200}
    assert account["holdings_confirmed_date"] == "2026-09-24"
    fill = client.post("/api/v1/accounts/real/fills", headers=auth(admin), json={
        "trade_date": "2026-09-25", "symbol": "600036", "side": "buy", "qty": 100, "price": "40.10"})
    assert fill.status_code == 201 and fill.json()["fees_estimated"]
    oversell = client.post("/api/v1/accounts/real/fills", headers=auth(admin), json={
        "trade_date": "2026-09-25", "symbol": "600000", "side": "sell", "qty": 5000, "price": "10"})
    assert oversell.status_code == 400 and oversell.json()["detail"]["code"] == "ledger"
    event_id = next(e["event_id"] for e in client.get("/api/v1/accounts/real/events", headers=auth(admin)).json()
                    if e["kind"] == "fill")
    assert client.post(f"/api/v1/position-events/{event_id}/reverse", headers=auth(admin),
                       json={"reason": "录错"}).status_code == 200
    holdings = client.get("/api/v1/accounts/real", headers=auth(admin)).json()["holdings"]
    assert "600036" not in {h["symbol"] for h in holdings}
    fills = client.get("/api/v1/accounts/real/fills", headers=auth(admin)).json()
    assert fills[0]["symbol"] == "600036"


def test_frontends_are_served_with_spa_fallback(tmp_path: Path) -> None:
    root = make_root(tmp_path / "root")
    for name, html in (("web", "<p>pc</p>"), ("mobile", "<p>mobile</p>")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "index.html").write_text(html, encoding="utf-8")
    (tmp_path / "web" / "app.js").write_text("console.log(1)", encoding="utf-8")
    settings = AppSettings(root=root, db_path=root / "app.sqlite", secret_key="s" * 48, web_dir=tmp_path / "web",
                           mobile_dir=tmp_path / "mobile")
    client = TestClient(create_app(settings))
    assert "pc" in client.get("/").text and "pc" in client.get("/decisions/x").text  # client-side route
    assert client.get("/app.js").text == "console.log(1)"
    assert "mobile" in client.get("/m/").text
    assert client.get("/api/v1/nope").status_code == 404
    assert client.get("/../secret").status_code in (200, 404) and "pc" in client.get("/%2e%2e/x").text


def test_health_meta_and_headers(env) -> None:
    _, _, client, _ = env
    response = client.get("/api/v1/health")
    assert response.json()["status"] == "ok"
    assert response.headers["X-Content-Type-Options"] == "nosniff" and response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/meta").json()["environment_label"] == "测试环境"


@pytest.mark.slow
@pytest.mark.skipif(not (ROOT / "data" / "market.duckdb").exists(), reason="local market database absent")
def test_market_endpoints_on_the_local_database(tmp_path: Path) -> None:
    settings = AppSettings(root=ROOT, db_path=tmp_path / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "viewer", ["viewer"])
    headers = auth(login(client, "viewer"))
    overview = client.get("/api/v1/market/overview", headers=headers).json()
    assert overview["breadth"]["traded"] > 4000 and overview["indices"]
    bars = client.get("/api/v1/instruments/600519/bars?limit=5", headers=headers).json()
    assert len(bars) == 5 and bars[-1]["close"] > 0
    assert client.get("/api/v1/instruments/600519", headers=headers).json()["industry"]["l1_name"]
    assert client.get("/api/v1/instruments/search?q=600519", headers=headers).json()[0]["symbol"] == "600519"
