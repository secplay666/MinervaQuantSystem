"""Invitation codes and self-registration."""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from quant_system.app.db import utc_now  # noqa: E402
from quant_system.app.db.models import AuditLog, Invitation, User  # noqa: E402
from test_api import PASSWORD, add_user, auth, env, login  # noqa: E402,F401  (fixtures)

GOOD = "Newcomer-pass-2026"


def register(client, code: str, username: str, password: str = GOOD):
    return client.post("/api/v1/auth/register", json={"code": code, "username": username,
                                                      "display_name": username, "password": password})


def test_invitations_register_users_with_their_roles(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "boss", ["admin"])
    add_user(sessions, "vic", ["viewer"])
    admin = auth(login(client, "boss"))
    assert client.post("/api/v1/invitations", headers=auth(login(client, "vic")), json={}).status_code == 403
    assert client.post("/api/v1/invitations", headers=admin, json={"roles": ["admin"]}).json()["detail"]["code"] \
        == "privileged_role"
    client.post("/api/v1/roles", headers=admin, json={"code": "helper", "name": "助理", "permissions": ["user:manage"]})
    assert client.post("/api/v1/invitations", headers=admin, json={"roles": ["helper"]}).status_code == 400

    created = client.post("/api/v1/invitations", headers=admin,
                          json={"roles": ["reviewer"], "max_uses": 2, "days": 7, "note": "研究组"}).json()
    code = created["code"]
    assert len(code) == 14 and created["hint"] == code[-4:] and created["state"] == "active"
    listed = client.get("/api/v1/invitations", headers=admin).json()
    assert "code" not in listed[0] and listed[0]["note"] == "研究组"

    assert register(client, "AAAA-BBBB-CCCC", "alice").json()["detail"]["code"] == "invalid_invitation"
    assert register(client, code, "alice", "short").json()["detail"]["code"] == "weak_password"
    assert register(client, code, "vic").status_code == 409  # taken username, the use is not consumed
    ok = register(client, f" {code.lower().replace('-', ' ')} ", "alice")  # typed loosely
    assert ok.status_code == 201 and ok.json()["roles"] == ["reviewer"]
    alice = login(client, "alice", GOOD)
    assert not alice["user"]["must_change_password"] and "decision:approve" in alice["user"]["permissions"]
    assert register(client, code, "bob").status_code == 201
    assert register(client, code, "carl").json()["detail"]["code"] == "invalid_invitation"  # used up

    listed = client.get("/api/v1/invitations", headers=admin).json()
    assert listed[0]["state"] == "used_up" and listed[0]["users"] == ["alice", "bob"]
    with sessions() as session:
        actions = set(session.scalars(select(AuditLog.action)))
        assert {"invitation.create", "user.register"} <= actions
        assert session.scalar(select(User).where(User.username == "alice")).invitation_id == created["id"]


def test_revoked_and_expired_codes_are_refused(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "boss", ["admin"])
    admin = auth(login(client, "boss"))
    first = client.post("/api/v1/invitations", headers=admin, json={"max_uses": 5}).json()
    second = client.post("/api/v1/invitations", headers=admin, json={"max_uses": 5}).json()
    assert client.post(f"/api/v1/invitations/{first['id']}/revoke", headers=admin).json()["state"] == "revoked"
    assert register(client, first["code"], "dora").status_code == 400
    with sessions() as session:
        session.get(Invitation, second["id"]).expires_at = utc_now() - timedelta(minutes=1)
        session.commit()
    assert register(client, second["code"], "dora").status_code == 400
    assert client.get("/api/v1/invitations", headers=admin).json()[0]["state"] == "expired"


def test_guessing_codes_is_throttled(env) -> None:
    _, _, client, _ = env
    for k in range(20):
        assert register(client, f"GUES-S{k:03d}-XXXX", f"user{k}").status_code == 400
    assert register(client, "GUES-S999-XXXX", "user99").status_code == 429
