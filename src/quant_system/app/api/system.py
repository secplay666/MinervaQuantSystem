"""/health, /meta and /system: liveness, environment label and data health."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db.models import DecisionRun, Event, EventPush
from ..deps import Principal, api_error, get_session, require, settings_of
from ..notify import send_test

router = APIRouter(tags=["系统"])
TEST_INTERVAL_SECONDS = 30.0  # WeCom robots accept 20 messages a minute; Server酱's free tier a few a day


def read_tsv(path: Path, limit: int) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return rows[-limit:][::-1]


def latest_manifests(root: Path, limit: int) -> list[dict]:
    out = []
    for path in sorted((root / "data" / "manifests").glob("*.json"), reverse=True)[:limit]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        out.append({k: payload.get(k) for k in ("run_id", "status", "mode", "started_at", "finished_at",
                                                 "expected_latest_date", "quality_summary", "catalog_status")})
    return out


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok", "environment": settings_of(request).environment}


@router.get("/meta")
def meta(request: Request) -> dict:
    settings = settings_of(request)
    return {"environment": settings.environment, "environment_label": settings.environment_label,
            "name": "Minerva 决策辅助", "api_version": "v1"}


@router.get("/system/status")
def status(request: Request, _: Principal = Depends(require("data:view")),
           session: Session = Depends(get_session)) -> dict:
    root = settings_of(request).root
    market = request.app.state.market
    runs = session.scalars(select(DecisionRun).where(DecisionRun.status != "superseded")
                           .order_by(DecisionRun.created_at.desc()).limit(10))
    return {
        "latest_session": market.latest_session().isoformat() if market.available() else None,
        "ingest_runs": latest_manifests(root, 5),
        "daily_jobs": read_tsv(root / "logs" / "daily" / "history.tsv", 15),
        "backups": read_tsv(root / "logs" / "backup" / "history.tsv", 15),
        "decisions": [{"run_id": r.run_id, "account_id": r.account_id, "trade_date": r.trade_date.isoformat(),
                       "kind": r.kind, "status": r.status,
                       "failed_gate": next((g["gate"] for g in r.gates if not g.get("passed")), None)}
                      for r in runs],
    }


@router.get("/system/notify")
def notify_status(request: Request, _: Principal = Depends(require("notify:manage")),
                  session: Session = Depends(get_session)) -> dict:
    """External channels (secrets masked; configured in the server's env file) and recent deliveries."""
    rows = session.execute(select(EventPush, Event.title, Event.level).join(Event, Event.event_id == EventPush.event_id)
                           .order_by(EventPush.attempted_at.desc(), EventPush.event_id).limit(30))
    return {**settings_of(request).notify.describe(),
            "deliveries": [{"event_id": push.event_id, "title": title, "level": level, "channel": push.channel,
                            "status": push.status, "attempts": push.attempts,
                            "attempted_at": push.attempted_at.isoformat(),
                            "sent_at": push.sent_at.isoformat() if push.sent_at else None, "error": push.error}
                           for push, title, level in rows]}


@router.post("/system/notify/test")
def notify_test(request: Request, principal: Principal = Depends(require("notify:manage")),
                session: Session = Depends(get_session)) -> dict:
    settings = settings_of(request)
    if not settings.notify.channels():
        raise api_error(400, "no_channel", "服务器上没有配置通知渠道")
    last = getattr(request.app.state, "notify_test_at", 0.0)
    if time.monotonic() - last < TEST_INTERVAL_SECONDS:
        raise api_error(429, "too_frequent", f"测试消息每 {TEST_INTERVAL_SECONDS:.0f} 秒最多发送一次")
    request.app.state.notify_test_at = time.monotonic()
    results = send_test(settings.notify, settings.environment_label)
    audit(session, principal.actor, "notify.test", "notify", ",".join(r.channel for r in results),
          after=[{"channel": r.channel, "status": r.status} for r in results], **principal.audit_kwargs())
    session.commit()
    return {"results": [{"channel": r.channel, "status": r.status, "error": r.error} for r in results]}
