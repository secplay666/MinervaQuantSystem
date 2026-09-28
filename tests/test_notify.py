"""Stage-4 P8: external notifications (app/notify.py)."""

from __future__ import annotations

import json
import urllib.parse
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database, utc_now  # noqa: E402
from quant_system.app.db.models import AuditLog, Event, EventPush  # noqa: E402
from quant_system.app.notify import (  # noqa: E402
    MAX_ATTEMPTS,
    NotifyConfig,
    _claim,
    _pending,
    dispatch,
    digest,
)

WECOM_KEY = "0123abcd-4567-89ef-0123-456789abcdef"
SCT_KEY = "SCT123456TabcdefABCDEF0123456789"


class Transport:
    """Records posts and answers like the service; ``fail`` makes the next posts raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, str]] = []
        self.fail = 0

    def __call__(self, url: str, body: bytes, content_type: str) -> bytes:
        self.calls.append((url, body, content_type))
        if self.fail:
            self.fail -= 1
            raise OSError(f"connection refused while posting to {url}")
        if "qyapi.weixin.qq.com" in url:
            return b'{"errcode": 0, "errmsg": "ok"}'
        return b'{"code": 0, "message": ""}'

    def wecom_content(self, k: int = -1) -> str:
        return json.loads(self.calls[k][1])["markdown"]["content"]


def add_event(session, event_id: str, level: str = "critical", category: str = "decision", title: str = "t",
              account: str | None = "paper1", age: timedelta = timedelta(minutes=5)) -> None:
    session.add(Event(event_id=event_id, at=utc_now() - age, level=level, category=category, title=title,
                      body="正文不外发", account_id=account))
    session.commit()


@pytest.fixture
def sessions(tmp_path: Path):
    _, factory = open_database(tmp_path / "app.sqlite")
    return factory


def test_config_parsing_and_masking() -> None:
    config = NotifyConfig.from_values({
        "MINERVA_NOTIFY_WECOM": f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}",
        "MINERVA_NOTIFY_SERVERCHAN": SCT_KEY, "MINERVA_NOTIFY_MIN_LEVEL": "warning"})
    assert config.wecom_key == WECOM_KEY and config.min_level == "warning" and not config.problems
    described = json.dumps(config.describe(), ensure_ascii=False)
    assert WECOM_KEY not in described and SCT_KEY not in described and "cdef" in described
    assert [c.name for c in config.channels()] == ["wecom", "serverchan"]

    bad = NotifyConfig.from_values({"MINERVA_NOTIFY_WECOM": "https://example.com/hook?key=x",
                                    "MINERVA_NOTIFY_SERVERCHAN": "short", "MINERVA_NOTIFY_MIN_LEVEL": "loud",
                                    "MINERVA_NOTIFY_LOOKBACK_HOURS": "0", "MINERVA_NOTIFY_LINK": "http://x"})
    assert bad.channels() == [] and bad.min_level == "info" and bad.lookback_hours == 24 and bad.link is None
    assert len(bad.problems) == 5
    assert NotifyConfig.from_values({}).channels() == []


def test_digest_hides_bodies_and_holdings(sessions) -> None:
    with sessions() as session:
        add_event(session, "a", "critical", "decision", "决策被闸门 G1 阻断")
        add_event(session, "b", "info", "decision", "交易清单已生成：卖出 3 笔，买入 5 笔，待审核")
        for k in range(3):
            add_event(session, f"r{k}", "warning" if k else "critical", "risk", f"60051{k} 今日跌停")
        events = list(session.scalars(select(Event)))
    title, lines = digest(events, "测试环境")
    text = "\n".join(lines)
    assert title == "Minerva 测试环境：严重 2 条，警告 2 条，消息 1 条"
    assert text.index("G1 阻断") < text.index("待审核")  # most severe first
    assert "6005" not in text and "正文不外发" not in text
    assert "paper1 持仓提示 3 条（严重 1，警告 2）" in text

    many = [Event(event_id=f"e{k}", at=utc_now(), level="info", category="decision", title=f"第 {k} 条")
            for k in range(20)]
    assert "另有 5 条" in "\n".join(digest(many, "测试环境", "https://8.8.8.8:20443/")[1])


def test_dispatch_sends_once_and_filters(sessions) -> None:
    config = NotifyConfig(wecom_key=WECOM_KEY, min_level="warning", lookback_hours=24)
    transport = Transport()
    with sessions() as session:
        add_event(session, "new", "critical")
        add_event(session, "quiet", "info")
        add_event(session, "old", "critical", age=timedelta(hours=30))
        [result] = dispatch(session, config, "测试环境", transport)
        assert (result.status, result.events) == ("sent", 1)
        assert transport.calls[0][0].endswith(WECOM_KEY)
        assert dispatch(session, config, "测试环境", transport)[0].status == "nothing"
        assert len(transport.calls) == 1
        add_event(session, "later", "warning")
        assert dispatch(session, config, "测试环境", transport)[0].events == 1
        pushes = {p.event_id: p.status for p in session.scalars(select(EventPush))}
    assert pushes == {"new": "sent", "later": "sent"}


def test_dispatch_retries_failures_without_leaking_the_key(sessions) -> None:
    config = NotifyConfig(wecom_key=WECOM_KEY, serverchan_key=SCT_KEY)
    transport = Transport()
    with sessions() as session:
        add_event(session, "e1")
        transport.fail = 1  # the first channel (wecom) fails, Server酱 succeeds
        results = {r.channel: r for r in dispatch(session, config, "测试环境", transport)}
        assert results["wecom"].status == "failed" and results["serverchan"].status == "sent"
        assert WECOM_KEY not in (results["wecom"].error or "") and "***" in (results["wecom"].error or "")
        stored = session.get(EventPush, ("e1", "wecom"))
        assert stored.status == "failed" and WECOM_KEY not in (stored.error or "")
        form = urllib.parse.parse_qs(transport.calls[1][1].decode("ascii"))
        assert transport.calls[1][0] == f"https://sctapi.ftqq.com/{SCT_KEY}.send" and form["title"][0]

        results = {r.channel: r for r in dispatch(session, config, "测试环境", transport)}
        assert results["wecom"].status == "sent" and results["serverchan"].status == "nothing"

        add_event(session, "e2")
        transport.fail = MAX_ATTEMPTS * 2
        for _ in range(MAX_ATTEMPTS + 1):
            dispatch(session, config, "测试环境", transport)
        push = session.get(EventPush, ("e2", "wecom"))
        assert push.status == "failed" and push.attempts == MAX_ATTEMPTS  # gave up


def test_lost_claims_are_retried(sessions) -> None:
    config = NotifyConfig(wecom_key=WECOM_KEY)
    transport = Transport()
    with sessions() as session:
        add_event(session, "e1")
        session.add(EventPush(event_id="e1", channel="wecom", status="sending", attempts=1,
                              attempted_at=utc_now() - timedelta(minutes=2)))
        session.commit()
        assert dispatch(session, config, "测试环境", transport)[0].status == "nothing"  # still in flight
        session.get(EventPush, ("e1", "wecom")).attempted_at = utc_now() - timedelta(minutes=11)
        session.commit()
        assert dispatch(session, config, "测试环境", transport)[0].status == "sent"


def test_serverchan3_address() -> None:
    [channel] = NotifyConfig(serverchan_key="sctp4242tABCDEFabcdef012345").channels()
    assert channel.url() == "https://4242.push.ft07.com/send/sctp4242tABCDEFabcdef012345.send"  # type: ignore[attr-defined]


def test_api_status_and_test_message(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import quant_system.app.notify as notify
    from quant_system.app.main import create_app
    from quant_system.app.settings import AppSettings
    from test_api import add_user, auth, login, make_root, tiny_market

    root = make_root(tmp_path / "root")
    tiny_market(root, ["600000"])
    transport = Transport()
    monkeypatch.setattr(notify, "http_post", transport)
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48,
                           notify=NotifyConfig(wecom_key=WECOM_KEY))
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "boss", ["admin"])
    add_user(sessions, "rev", ["reviewer"])
    admin, reviewer = auth(login(client, "boss")), auth(login(client, "rev"))

    assert client.get("/api/v1/system/notify", headers=reviewer).status_code == 403
    status = client.get("/api/v1/system/notify", headers=admin).json()
    assert status["channels"][0]["name"] == "wecom" and WECOM_KEY not in json.dumps(status)
    assert client.post("/api/v1/system/notify/test", headers=reviewer).status_code == 403
    response = client.post("/api/v1/system/notify/test", headers=admin)
    assert response.status_code == 200 and response.json()["results"][0]["status"] == "sent"
    assert "测试消息" in transport.wecom_content()
    assert client.post("/api/v1/system/notify/test", headers=admin).status_code == 429
    with sessions() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == "notify.test")) is not None


def test_cli_event_add_and_dispatch(tmp_path: Path, monkeypatch, capsys) -> None:
    import quant_system.app.notify as notify
    from quant_system.app.cli import build_parser

    env = tmp_path / "app.env"
    env.write_text(f"MINERVA_NOTIFY_WECOM={WECOM_KEY}\n", encoding="utf-8")  # no secret: not needed here
    monkeypatch.delenv("MINERVA_SECRET_KEY", raising=False)
    monkeypatch.setenv("MINERVA_DB", str(tmp_path / "app.sqlite"))
    transport = Transport()
    monkeypatch.setattr(notify, "http_post", transport)

    def run(*argv: str) -> int:
        args = build_parser().parse_args(["--root", str(tmp_path), "--env-file", str(env), *argv])
        return args.handler(args)

    assert run("event", "add", "--level", "critical", "--category", "data", "--title", "每日数据采集未完成",
               "--id", "ingest-x") == 0
    assert run("event", "add", "--level", "critical", "--category", "data", "--title", "重复", "--id",
               "ingest-x") == 0
    assert run("notify", "dispatch") == 0
    assert "每日数据采集未完成" in transport.wecom_content() and "重复" not in transport.wecom_content()
    assert run("notify", "dispatch") == 0 and len(transport.calls) == 1
    capsys.readouterr()
    assert run("notify", "status") == 0
    assert WECOM_KEY not in capsys.readouterr().out


def test_events_between_dispatches_are_not_lost_to_the_lookback(sessions) -> None:
    config = NotifyConfig(wecom_key=WECOM_KEY, lookback_hours=24)
    transport = Transport()
    friday = utc_now() - timedelta(days=3)
    with sessions() as session:
        add_event(session, "fri-evening", age=timedelta(days=3, hours=1))
        assert dispatch(session, config, "测试环境", transport, now=friday)[0].events == 1
        add_event(session, "fri-late", age=timedelta(days=2, hours=22))  # after Friday's dispatch
        add_event(session, "ancient", age=timedelta(days=40))
        [result] = dispatch(session, config, "测试环境", transport)  # Monday: 70 hours later
        assert (result.status, result.events) == ("sent", 1)
        assert session.get(EventPush, ("fri-late", "wecom")).status == "sent"
        assert session.get(EventPush, ("ancient", "wecom")) is None


def test_a_row_claimed_elsewhere_is_left_out(sessions) -> None:
    config = NotifyConfig(wecom_key=WECOM_KEY)
    with sessions() as first, sessions() as second:
        add_event(first, "e1")
        first.add(EventPush(event_id="e1", channel="wecom", status="failed", attempts=1, attempted_at=utc_now()))
        first.commit()
        pending = _pending(first, "wecom", config, utc_now())
        assert [(e.event_id, seen) for e, seen in pending] == [("e1", ("failed", 1))]
        other = second.get(EventPush, ("e1", "wecom"))
        other.status, other.attempts = "sending", 2  # the other dispatcher got there first
        second.commit()
        assert _claim(first, "wecom", pending, utc_now()) == []
        first.expire_all()
        assert first.get(EventPush, ("e1", "wecom")).attempts == 2
