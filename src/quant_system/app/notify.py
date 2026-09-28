"""External notifications (stage-4 P8): new events are pushed as one digest per
dispatch to a WeCom group robot and/or Server酱, configured in the env file
(``~/.config/minerva/app.env``).  Nothing is sent unless a channel is
configured.

Only event titles leave the server; risk alerts, whose titles name held
securities, are reduced to a count per account.  Bodies, amounts and holdings
stay in the app.  Delivery is tracked per event and channel in
``event_pushes``: a failed digest is retried by later dispatches (up to
MAX_ATTEMPTS), each dispatch picks up what arrived since the previous one,
and a newly enabled channel starts with the lookback window only, so it does
not replay history.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db.base import utc_now
from .db.models import Event, EventPush

LEVELS = {"info": 0, "warning": 1, "critical": 2}
LEVEL_NAMES = {"critical": "严重", "warning": "警告", "info": "消息"}
MAX_ATTEMPTS = 3
STALE_SENDING = timedelta(minutes=10)  # a "sending" claim older than this was lost (crash) and is retried
MAX_CATCH_UP = timedelta(days=14)  # events after a long gap between dispatches (holidays, a paused timer)
MAX_LINES = 15
WECOM_PREFIX = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
WECOM_MAX_BYTES = 4096
CST = ZoneInfo("Asia/Shanghai")

Post = Callable[[str, bytes, str], bytes]  # (url, body, content type) -> response body


class NotifyError(RuntimeError):
    pass


def http_post(url: str, body: bytes, content_type: str, timeout: float = 10.0) -> bytes:
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type, "User-Agent": "minerva-notify"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
        return response.read()


def _mask(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 8 else "…"


def _clip_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[: limit - 3].decode("utf-8", "ignore") + "…"


class Channel(Protocol):
    name: str

    def label(self) -> str: ...

    def send(self, title: str, lines: list[str]) -> None: ...


@dataclass(frozen=True)
class _HttpChannel:
    secret: str
    post: Post = http_post

    def _call(self, url: str, body: bytes, content_type: str) -> dict:
        try:
            reply = self.post(url, body, content_type)
            return json.loads(reply.decode("utf-8"))
        except Exception as exc:  # network, HTTP status or a non-JSON reply
            raise NotifyError(f"{type(exc).__name__}: {exc}".replace(self.secret, "***")) from None


@dataclass(frozen=True)
class WeComRobot(_HttpChannel):
    """企业微信群机器人: markdown message, at most 4096 bytes, 20 messages a minute."""

    name: ClassVar[str] = "wecom"

    def label(self) -> str:
        return f"企业微信群机器人（key {_mask(self.secret)}）"

    def send(self, title: str, lines: list[str]) -> None:
        content = _clip_bytes("\n".join([f"**{title}**", *lines]), WECOM_MAX_BYTES)
        body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}, ensure_ascii=False)
        reply = self._call(WECOM_PREFIX + self.secret, body.encode("utf-8"), "application/json")
        if reply.get("errcode") != 0:
            raise NotifyError(f"企业微信返回 errcode {reply.get('errcode')}: {reply.get('errmsg')}")


@dataclass(frozen=True)
class ServerChan(_HttpChannel):
    """Server酱 (Turbo ``SCT…`` or Server酱³ ``sctp<uid>t…`` SendKey): pushes to WeChat."""

    name: ClassVar[str] = "serverchan"

    def label(self) -> str:
        return f"Server酱（SendKey {_mask(self.secret)}）"

    def url(self) -> str:
        match = re.match(r"sctp(\d+)t", self.secret)
        if match:
            return f"https://{match.group(1)}.push.ft07.com/send/{self.secret}.send"
        return f"https://sctapi.ftqq.com/{self.secret}.send"

    def send(self, title: str, lines: list[str]) -> None:
        body = urllib.parse.urlencode({"title": title[:32], "desp": "\n".join(lines)}).encode("ascii")
        reply = self._call(self.url(), body, "application/x-www-form-urlencoded")
        if reply.get("code") != 0:
            raise NotifyError(f"Server酱返回 code {reply.get('code')}: {reply.get('message')}")


@dataclass(frozen=True)
class NotifyConfig:
    wecom_key: str | None = None
    serverchan_key: str | None = None
    min_level: str = "info"
    lookback_hours: int = 24
    link: str | None = None  # optional address appended to each digest
    problems: tuple[str, ...] = ()

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> NotifyConfig:
        """MINERVA_NOTIFY_WECOM (the robot's key, or its whole webhook address),
        MINERVA_NOTIFY_SERVERCHAN (SendKey), MINERVA_NOTIFY_MIN_LEVEL
        (info | warning | critical), MINERVA_NOTIFY_LOOKBACK_HOURS and
        MINERVA_NOTIFY_LINK.  Invalid values disable that setting and are
        listed in ``problems`` instead of stopping the API."""
        problems: list[str] = []
        wecom = values.get("MINERVA_NOTIFY_WECOM", "").strip() or None
        if wecom and wecom.startswith("https://"):
            if wecom.startswith(WECOM_PREFIX):
                wecom = wecom[len(WECOM_PREFIX):]
            else:
                problems.append(f"MINERVA_NOTIFY_WECOM 不是企业微信机器人地址（应以 {WECOM_PREFIX} 开头），已忽略")
                wecom = None
        if wecom and not re.fullmatch(r"[A-Za-z0-9-]{16,64}", wecom):
            problems.append("MINERVA_NOTIFY_WECOM 的 key 格式不对，已忽略")
            wecom = None
        serverchan = values.get("MINERVA_NOTIFY_SERVERCHAN", "").strip() or None
        if serverchan and not re.fullmatch(r"[A-Za-z0-9]{16,80}", serverchan):
            problems.append("MINERVA_NOTIFY_SERVERCHAN 的 SendKey 格式不对，已忽略")
            serverchan = None
        level = values.get("MINERVA_NOTIFY_MIN_LEVEL", "info").strip() or "info"
        if level not in LEVELS:
            problems.append(f"MINERVA_NOTIFY_MIN_LEVEL={level} 无效，按 info 处理")
            level = "info"
        try:
            lookback = int(values.get("MINERVA_NOTIFY_LOOKBACK_HOURS", "24"))
            if not 1 <= lookback <= 24 * 14:
                raise ValueError
        except ValueError:
            problems.append("MINERVA_NOTIFY_LOOKBACK_HOURS 应为 1–336 的整数，按 24 处理")
            lookback = 24
        link = values.get("MINERVA_NOTIFY_LINK", "").strip() or None
        if link and not link.startswith("https://"):
            problems.append("MINERVA_NOTIFY_LINK 应以 https:// 开头，已忽略")
            link = None
        return cls(wecom, serverchan, level, lookback, link, tuple(problems))

    def channels(self, post: Post | None = None) -> list[Channel]:
        post = post or http_post  # looked up at call time, so tests can replace it
        out: list[Channel] = []
        if self.wecom_key:
            out.append(WeComRobot(self.wecom_key, post))
        if self.serverchan_key:
            out.append(ServerChan(self.serverchan_key, post))
        return out

    def describe(self) -> dict:
        """What the settings page shows: no secrets, only their last characters."""
        return {"channels": [{"name": c.name, "label": c.label()} for c in self.channels()],
                "min_level": self.min_level, "lookback_hours": self.lookback_hours, "link": self.link,
                "problems": list(self.problems)}


# -- digest ---------------------------------------------------------------------------


def digest(events: list[Event], environment_label: str, link: str | None = None) -> tuple[str, list[str]]:
    """Title and markdown lines for a batch of events, most severe first."""
    counts = Counter(e.level for e in events)
    summary = "，".join(f"{LEVEL_NAMES[level]} {counts[level]} 条" for level in ("critical", "warning", "info")
                       if counts[level])
    title = f"Minerva {environment_label}：{summary}"
    ordered = sorted(events, key=lambda e: (-LEVELS.get(e.level, 0), e.at, e.event_id))
    lines: list[str] = []
    risk: dict[str, Counter] = {}
    for event in ordered:
        if event.category == "risk":  # titles name held securities: count them per account instead
            risk.setdefault(event.account_id or "—", Counter())[event.level] += 1
            continue
        at = event.at.astimezone(CST).strftime("%m-%d %H:%M")
        where = f"{event.account_id} " if event.account_id else ""
        lines.append(f"- 【{LEVEL_NAMES.get(event.level, event.level)}】{at} {where}{event.title}")
    shown = lines[:MAX_LINES]
    if len(lines) > MAX_LINES:
        shown.append(f"- …另有 {len(lines) - MAX_LINES} 条")
    for account, levels in sorted(risk.items()):
        detail = "，".join(f"{LEVEL_NAMES[lv]} {levels[lv]}" for lv in ("critical", "warning", "info") if levels[lv])
        shown.append(f"- {account} 持仓提示 {sum(levels.values())} 条（{detail}）")
    shown.append("")
    shown.append(f"[打开 Minerva 查看详情]({link})" if link else "详情请在 Minerva App 或网页的通知中心查看")
    return title, shown


# -- dispatch -------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    channel: str
    status: str  # sent | failed | nothing | busy
    events: int = 0
    error: str | None = None


Seen = tuple[str, int] | None  # (status, attempts) of the delivery row when read; None: no row yet


def _pending(session: Session, channel: str, config: NotifyConfig, now: datetime) -> list[tuple[Event, Seen]]:
    """Events not yet delivered on ``channel``, with the state of their row:
    new ones since the channel's previous dispatch (less the lookback, and
    never more than MAX_CATCH_UP back; only the lookback when the channel is
    new), plus failed ones with attempts left, whatever their age."""
    lookback = timedelta(hours=config.lookback_hours)
    last = session.scalar(select(func.max(EventPush.attempted_at)).where(EventPush.channel == channel))
    since = now - lookback if last is None else max(min(now, last) - lookback, now - MAX_CATCH_UP)
    retry = select(EventPush.event_id).where(EventPush.channel == channel, EventPush.status != "sent",
                                             EventPush.attempts < MAX_ATTEMPTS)
    threshold = LEVELS[config.min_level]
    events = [e for e in session.scalars(select(Event).where(or_(Event.at >= since, Event.event_id.in_(retry)))
                                         .order_by(Event.at, Event.event_id))
              if LEVELS.get(e.level, 0) >= threshold]
    rows = {event_id: (status, attempts, attempted_at) for event_id, status, attempts, attempted_at in session.execute(
        select(EventPush.event_id, EventPush.status, EventPush.attempts, EventPush.attempted_at).where(
            EventPush.channel == channel, EventPush.event_id.in_([e.event_id for e in events])))}
    out: list[tuple[Event, Seen]] = []
    for event in events:
        row = rows.get(event.event_id)
        if row is None:
            out.append((event, None))
            continue
        status, attempts, attempted_at = row
        if status == "sent" or attempts >= MAX_ATTEMPTS:
            continue
        if status == "failed" or attempted_at < now - STALE_SENDING:
            out.append((event, (status, attempts)))
    return out


def _claim(session: Session, channel: str, pending: list[tuple[Event, Seen]], now: datetime) -> list[Event]:
    """Mark the events as being sent on ``channel``, provided their row is
    still as it was read: a row another dispatcher changed meanwhile is left
    out, and a new row it inserted first abandons the round (nothing claimed)."""
    claimed = []
    for event, seen in pending:
        if seen is None:
            session.add(EventPush(event_id=event.event_id, channel=channel, status="sending", attempts=1,
                                  attempted_at=now))
            try:
                session.flush()
            except IntegrityError:  # another dispatcher is at work: leave this round to it
                session.rollback()
                return []
        else:
            status, attempts = seen
            taken = session.execute(update(EventPush).where(
                EventPush.event_id == event.event_id, EventPush.channel == channel, EventPush.status == status,
                EventPush.attempts == attempts).values(status="sending", attempts=attempts + 1, attempted_at=now,
                                                       error=None))
            if taken.rowcount != 1:
                continue
        claimed.append(event)
    session.commit()
    return claimed


def _finish(session: Session, channel: str, events: list[Event], **values: object) -> None:
    session.execute(update(EventPush).where(EventPush.channel == channel,
                                            EventPush.event_id.in_([e.event_id for e in events])).values(**values))
    session.commit()


def dispatch(session: Session, config: NotifyConfig, environment_label: str, post: Post | None = None,
             now: datetime | None = None) -> list[DispatchResult]:
    """Send one digest of the due events to every configured channel."""
    now = now or utc_now()
    results = []
    for channel in config.channels(post):
        pending = _pending(session, channel.name, config, now)
        events = _claim(session, channel.name, pending, now) if pending else []
        if not events:
            results.append(DispatchResult(channel.name, "busy" if pending else "nothing"))
            continue
        title, lines = digest(events, environment_label, config.link)
        try:
            channel.send(title, lines)
        except NotifyError as exc:
            _finish(session, channel.name, events, status="failed", error=str(exc)[:500])
            results.append(DispatchResult(channel.name, "failed", len(events), str(exc)))
            continue
        _finish(session, channel.name, events, status="sent", sent_at=utc_now())
        results.append(DispatchResult(channel.name, "sent", len(events)))
    return results


def send_test(config: NotifyConfig, environment_label: str, post: Post | None = None) -> list[DispatchResult]:
    results = []
    at = utc_now().astimezone(CST).strftime("%Y-%m-%d %H:%M")
    for channel in config.channels(post):
        try:
            channel.send(f"Minerva {environment_label}：测试消息", [f"- {at} 通知渠道配置成功", "",
                                                                  "以后每日决策、阻断和失败会推送到这里"])
            results.append(DispatchResult(channel.name, "sent"))
        except NotifyError as exc:
            results.append(DispatchResult(channel.name, "failed", error=str(exc)))
    return results
