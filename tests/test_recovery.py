"""Recovery of reported failures (app/recovery.py): a later successful run closes the open critical events, the
home page's 待办 drops them, the notification centre marks them, and one info event says so."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database, utc_now  # noqa: E402
from quant_system.app.db.models import Event  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.recovery import open_critical, recover  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from test_api import add_user, auth, login  # noqa: E402
from test_decision import make_root  # noqa: E402


def failure(event_id: str, title: str, category: str = "data") -> Event:
    return Event(event_id=event_id, at=utc_now(), level="critical", category=category, title=title)


def test_the_cli_closes_the_open_failures_of_a_prefix_once(tmp_path: Path, monkeypatch, capsys) -> None:
    from quant_system.app.cli import build_parser

    monkeypatch.delenv("MINERVA_SECRET_KEY", raising=False)
    monkeypatch.setenv("MINERVA_DB", str(tmp_path / "app.sqlite"))
    _, sessions = open_database(tmp_path / "app.sqlite")
    with sessions() as session:
        session.add_all([failure("ingest-2026-10-28-1", "每日数据采集未完成（退出码 2）"),
                         failure("backup-2026-10-28-1", "数据备份失败（退出码 1）")])
        session.commit()

    def resolve() -> str:
        args = build_parser().parse_args(["--root", str(tmp_path), "event", "resolve", "--category", "data",
                                          "--prefix", "ingest-", "--id", "recovered-ingest-2026-10-28-2",
                                          "--title", "每日数据采集已恢复", "--resolution", "10-28 21:47 开始的采集完整完成"])
        assert args.handler(args) == 0
        return capsys.readouterr().out.strip()

    assert resolve() == "1"
    assert resolve() == "0"  # nothing open any more: no second recovery event
    with sessions() as session:
        events = {e.event_id: e for e in session.scalars(select(Event))}
    assert events["ingest-2026-10-28-1"].resolved_at is not None
    assert events["ingest-2026-10-28-1"].resolution == "10-28 21:47 开始的采集完整完成"
    assert events["backup-2026-10-28-1"].resolved_at is None  # another prefix stays open
    recovered = events["recovered-ingest-2026-10-28-2"]
    assert recovered.level == "info" and "每日数据采集未完成" in recovered.body


def test_resolved_failures_leave_the_todo_and_are_marked_in_the_list(tmp_path: Path) -> None:
    root = make_root(tmp_path / "root")
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    alice = auth(login(client, "alice"))
    with sessions() as session:
        session.add_all([failure("ingest-1", "每日数据采集未完成（退出码 2）"),
                         failure("dr-x-blocked", "决策被闸门 G1 阻断", category="decision")])
        session.commit()
    titles = {e["title"] for e in client.get("/api/v1/todo", headers=alice).json()["critical_unread"]}
    assert titles == {"每日数据采集未完成（退出码 2）", "决策被闸门 G1 阻断"}

    with sessions() as session:
        recover(session, open_critical(category="data", prefix="ingest-"), "recovered-1", "每日数据采集已恢复",
                "重试完成", category="data")
        session.commit()
    titles = {e["title"] for e in client.get("/api/v1/todo", headers=alice).json()["critical_unread"]}
    assert titles == {"决策被闸门 G1 阻断"}
    items = {e["event_id"]: e for e in client.get("/api/v1/events", headers=alice).json()["items"]}
    assert items["ingest-1"]["resolved_at"] and items["ingest-1"]["resolution"] == "重试完成"
    assert items["dr-x-blocked"]["resolved_at"] is None
    assert items["recovered-1"]["level"] == "info"
