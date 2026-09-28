"""/events: the notification centre (list, read state, WebSocket push)."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..db.models import Event, EventRead, User
from ..deps import Principal, get_session, require
from ..rbac import user_permissions
from ..security import TokenError, decode_access_token

router = APIRouter(tags=["通知"])
view = require("event:view")
PUSH_INTERVAL_SECONDS = 3.0


def event_view(event: Event, read: bool) -> dict:
    return {"event_id": event.event_id, "at": event.at.isoformat(), "level": event.level,
            "category": event.category, "title": event.title, "body": event.body, "run_id": event.run_id,
            "trade_date": event.trade_date.isoformat() if event.trade_date else None,
            "account_id": event.account_id, "symbol": event.symbol, "action_hint": event.action_hint, "read": read}


@router.get("/events")
def list_events(level: str | None = None, category: str | None = None, account_id: str | None = None,
                unread: bool = False, before: datetime | None = None, limit: int = 50,
                principal: Principal = Depends(view), session: Session = Depends(get_session)) -> dict:
    read_ids = set(session.scalars(select(EventRead.event_id).where(EventRead.user_id == principal.user.id)))
    query = select(Event)
    if level:
        query = query.where(Event.level == level)
    if category:
        query = query.where(Event.category == category)
    if account_id:
        query = query.where(Event.account_id == account_id)
    if before:
        query = query.where(Event.at < before)
    if unread and read_ids:
        query = query.where(Event.event_id.not_in(read_ids))
    rows = session.scalars(query.order_by(Event.at.desc(), Event.event_id).limit(min(limit, 200)))
    all_ids = set(session.scalars(select(Event.event_id)))
    return {"unread": len(all_ids - read_ids), "items": [event_view(e, e.event_id in read_ids) for e in rows]}


@router.post("/events/{event_id}/read")
def mark_read(event_id: str, principal: Principal = Depends(view), session: Session = Depends(get_session)) -> dict:
    if session.get(Event, event_id) is not None and session.get(EventRead, (event_id, principal.user.id)) is None:
        session.add(EventRead(event_id=event_id, user_id=principal.user.id, read_at=utc_now()))
        session.commit()
    return {"ok": True}


@router.post("/events/read-all")
def mark_all_read(principal: Principal = Depends(view), session: Session = Depends(get_session)) -> dict:
    read_ids = set(session.scalars(select(EventRead.event_id).where(EventRead.user_id == principal.user.id)))
    now = utc_now()
    for event_id in session.scalars(select(Event.event_id)):
        if event_id not in read_ids:
            session.add(EventRead(event_id=event_id, user_id=principal.user.id, read_at=now))
    session.commit()
    return {"ok": True}


@router.websocket("/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    """Pushes new events.  Browsers cannot set headers on a WebSocket, so the
    access token comes as the first message: {"token": "..."}."""
    await websocket.accept()
    app = websocket.app
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        claims = decode_access_token(app.state.settings.secret_key, str(first.get("token", "")))
    except (asyncio.TimeoutError, TokenError, ValueError, KeyError, WebSocketDisconnect):
        await websocket.close(code=4401)
        return
    with app.state.sessions() as session:
        user = session.get(User, int(claims["sub"]))
        if user is None or not user.is_active or "event:view" not in user_permissions(session, user.id):
            await websocket.close(code=4403)
            return
        last = session.scalar(select(Event.at).order_by(Event.at.desc()).limit(1)) or utc_now()
    await websocket.send_json({"type": "ready"})
    try:
        while True:
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
            with app.state.sessions() as session:
                rows = list(session.scalars(select(Event).where(Event.at > last).order_by(Event.at)))
            for event in rows:
                await websocket.send_json({"type": "event", "event": event_view(event, False)})
                last = max(last, event.at)
    except WebSocketDisconnect:
        return
