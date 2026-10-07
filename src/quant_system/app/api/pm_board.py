"""Position manager API, part 3: the board, signals, settings, stage views and the stage backtest."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...position.rules import RuleParams
from ...position.stages import PRESETS, STAGE_KEYS, STAGE_NAMES, StageParams, classify, stage_view
from ..audit import audit
from ..db.base import utc_now
from ..db.models import PmItem, PmSignal, PmSettings
from ..deps import Principal, api_error, get_session
from ..position import (
    LABELS, POOLS, PRIORITY_NAMES, PositionError, campaign_rows, evaluate_user, index_band, instrument_kind,
    load_series, report_tiers, rule_params, shown_view, stage_params, summary_row, sync_signals, today_signals,
    top_facts, user_settings,
)
from .pm import market_of, safe, use

router = APIRouter(tags=["仓位管家"])


def signal_rows(session: Session, user_id: int, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
    query = (select(PmSignal, PmItem).join(PmItem, PmItem.id == PmSignal.item_id).where(PmSignal.user_id == user_id)
             .order_by(PmSignal.trade_date.desc(), PmSignal.priority, PmSignal.id.desc()).limit(limit))
    if unread_only:
        query = query.where(PmSignal.read_at.is_(None))
    return [{"id": s.id, "item_id": s.item_id, "symbol": item.symbol, "name": item.name, "trade_date": s.trade_date,
             "rule": s.rule, "priority": s.priority, "priority_name": PRIORITY_NAMES.get(s.priority),
             "message": s.message, "payload": s.payload, "read": s.read_at is not None}
            for s, item in session.execute(query)]


def unread_count(session: Session, user_id: int) -> int:
    return int(session.scalar(select(func.count()).select_from(PmSignal)
                              .where(PmSignal.user_id == user_id, PmSignal.read_at.is_(None))) or 0)


@router.get("/pm/board")
def board(request: Request, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    """Label counts, the main indices, every item's state and the latest signals.
    Viewing it also stores the events of sessions not evaluated yet."""
    market = market_of(request)
    user_id = principal.user.id
    results, settings = evaluate_user(session, market, user_id)
    new = sync_signals(session, user_id, results, settings)
    rows = [summary_row(item, result, settings) for item, result in results]
    latest = max((r["latest"] for r in rows if r["latest"]), default=None)
    out = {"label_mode": settings.label_mode, "latest": latest,
           "counts": {label: sum(1 for r in rows if r["label"] == label) for label in LABELS},
           "pools": {pool: sum(1 for r in rows if r["pool"] == pool) for pool in POOLS if pool != "archived"},
           "tiers": report_tiers(rows, today_signals(session, user_id, latest)),
           "indices": index_band(session, market, user_id, settings), "items": rows,
           "signals": signal_rows(session, user_id, limit=50), "new_signals": len(new),
           "unread": unread_count(session, user_id)}
    session.commit()
    return safe(out)


@router.get("/pm/signals")
def list_signals(unread: bool = False, limit: int = Query(default=200, ge=1, le=1000),
                 principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    return safe({"signals": signal_rows(session, principal.user.id, unread_only=unread, limit=limit),
                 "unread": unread_count(session, principal.user.id)})


class SignalsRead(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=1000)  # None: all of them


@router.post("/pm/signals/read")
def mark_read(body: SignalsRead, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    statement = update(PmSignal).where(PmSignal.user_id == principal.user.id, PmSignal.read_at.is_(None))
    if body.ids is not None:
        statement = statement.where(PmSignal.id.in_(body.ids))
    marked = session.execute(statement.values(read_at=utc_now())).rowcount
    session.commit()
    return {"marked": marked, "unread": unread_count(session, principal.user.id)}


def settings_view(settings: PmSettings) -> dict[str, Any]:
    return {"stage_preset": settings.stage_preset, "stage_overrides": settings.stage_params or {},
            "stage_params": stage_params(settings).to_dict(), "rule_overrides": settings.rule_params or {},
            "rule_params": rule_params(settings).to_dict(), "rule_defaults": RuleParams().to_dict(),
            "label_mode": settings.label_mode, "push_daily": settings.push_daily, "auto_base": settings.auto_base,
            "crowd_high": settings.crowd_high, "crowd_low": settings.crowd_low,
            "evaluated_through": settings.evaluated_through,
            "presets": [{"key": key, "name": name, "params": params.to_dict()} for key, (name, params) in PRESETS.items()]}


AUDITED_SETTINGS = ("stage_preset", "stage_overrides", "rule_overrides", "label_mode", "push_daily", "auto_base",
                    "crowd_high", "crowd_low")


class SettingsIn(BaseModel):
    stage_preset: str | None = None
    stage_params: dict[str, float] | None = None  # changes to the preset; {} goes back to it
    rule_params: dict[str, Any] | None = None     # changes to the defaults; {} goes back to them
    label_mode: str | None = None                 # suggest | manual
    push_daily: bool | None = None
    auto_base: bool | None = None                 # a close in the 起涨区 turns 左侧 into 筑底
    crowd_high: float | None = None               # industry crowding reminder (money map) ...
    crowd_low: float | None = None                # ... and its relief


@router.get("/pm/settings")
def get_settings(principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    out = safe(settings_view(user_settings(session, principal.user.id)))
    session.commit()
    return out


@router.put("/pm/settings")
def put_settings(body: SettingsIn, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    settings = user_settings(session, principal.user.id)
    before = safe(settings_view(settings))
    preset = body.stage_preset or settings.stage_preset
    if preset not in PRESETS:
        raise api_error(400, "invalid", f"未知的阶段预设 {preset}")
    # Another preset starts from its own values unless changes come with it.
    overrides = body.stage_params if body.stage_params is not None else (
        {} if preset != settings.stage_preset else settings.stage_params or {})
    rules = body.rule_params if body.rule_params is not None else settings.rule_params or {}
    if body.label_mode not in (None, "suggest", "manual"):
        raise api_error(400, "invalid", "标签模式只能是 suggest（系统给观点）或 manual（手工）")
    high = body.crowd_high if body.crowd_high is not None else settings.crowd_high
    low = body.crowd_low if body.crowd_low is not None else settings.crowd_low
    if not 0.5 <= low < high <= 5:
        raise api_error(400, "invalid", "行业拥挤提醒：解除线要低于提醒线，并在 0.5 到 5 之间")
    try:
        StageParams.from_dict(overrides, PRESETS[preset][1])
        RuleParams.from_dict(rules)
    except (TypeError, ValueError) as exc:
        raise api_error(400, "invalid", str(exc)) from None
    settings.stage_preset, settings.stage_params, settings.rule_params = preset, dict(overrides), dict(rules)
    if body.label_mode is not None:
        settings.label_mode = body.label_mode
    if body.push_daily is not None:
        settings.push_daily = body.push_daily
    if body.auto_base is not None:
        settings.auto_base = body.auto_base
    settings.crowd_high, settings.crowd_low = high, low
    settings.updated_at = utc_now()
    after = safe(settings_view(settings))
    audit(session, principal.actor, "pm.settings", "pm_settings", str(principal.user.id),
          before={k: before[k] for k in AUDITED_SETTINGS}, after={k: after[k] for k in AUDITED_SETTINGS},
          **principal.audit_kwargs())
    session.commit()
    return after


@router.get("/pm/stage/{symbol}")
def stage_history(symbol: str, request: Request, preset: str | None = None, principal: Principal = Depends(use),
                  session: Session = Depends(get_session)) -> dict:
    """The stage view of any code and its stages over time (bands on the chart),
    with the user's parameters or a preset's."""
    market = market_of(request)
    if preset is not None and preset not in PRESETS:
        raise api_error(404, "not_found", f"未知的阶段预设 {preset}")
    params = PRESETS[preset][1] if preset else stage_params(user_settings(session, principal.user.id))
    session.commit()
    try:
        kind, name = instrument_kind(market, symbol)
    except PositionError as exc:
        raise api_error(404, "not_found", str(exc)) from None
    series = load_series(market, symbol, kind)
    segments: list[dict] = []
    if series.dates:
        stages = classify(series.close, params.for_index() if kind == "index" else params)
        for day, stage in zip(series.dates, stages):
            key = STAGE_KEYS[int(stage)]
            if segments and segments[-1]["stage"] == key:
                segments[-1]["end"] = day
            else:
                segments.append({"start": day, "end": day, "stage": key, "name": STAGE_NAMES[int(stage)]})
    view = stage_view(series.close, params, is_index=kind == "index") if series.dates else None
    return safe({"symbol": symbol, "name": name, "kind": kind, "params": params.to_dict(),
                 "view": shown_view(view, series), "segments": segments})


class BacktestIn(BaseModel):
    preset: str = "steady"
    params: dict[str, float] = Field(default_factory=dict)  # changes to the preset
    start: date | None = None
    end: date | None = None


@router.post("/pm/stage-backtest", status_code=202)
def start_backtest(body: BacktestIn, request: Request, principal: Principal = Depends(use)) -> dict:
    market_of(request)
    if body.preset not in PRESETS:
        raise api_error(400, "invalid", f"未知的阶段预设 {body.preset}")
    if body.start and body.end and body.start >= body.end:
        raise api_error(400, "invalid", "开始日须早于结束日")
    try:
        params = StageParams.from_dict(body.params, PRESETS[body.preset][1])
    except (TypeError, ValueError) as exc:
        raise api_error(400, "invalid", str(exc)) from None
    job = request.app.state.stage_jobs.submit(principal.user.id, params, body.start, body.end)
    return safe(job.view())


@router.get("/pm/jobs/{job_id}")
def get_job(job_id: str, request: Request, principal: Principal = Depends(use)) -> dict:
    job = request.app.state.stage_jobs.get(job_id, principal.user.id)
    if job is None:
        raise api_error(404, "not_found", "任务不存在（服务重启后需要重新运行）")
    return safe(job.view())


RECENT_DAYS = 7  # 突破确立 keeps what left the list this many days


@router.get("/pm/lists/breakouts")
def breakouts(request: Request, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    """突破确立 (design §11.5): resonant (entered), waiting for the index, and what left lately."""
    market = market_of(request)
    results, settings = evaluate_user(session, market, principal.user.id)
    rows = []
    for item, result in results:
        row = summary_row(item, result, settings)
        row["from_breakout"] = row["close"] / row["breakout_close"] - 1 \
            if row["close"] and row["breakout_close"] else None
        rows.append(row)
    latest = max((r["latest"] for r in rows if r["latest"]), default=None)
    since = latest - timedelta(days=RECENT_DAYS) if latest else None
    resonant = [r for r in rows if r["activated_on"] and r["weight"] > 0]
    waiting = [r for r in rows if r["activated_on"] and not r["entered"] and r["phase"] in ("active", "realized")]
    removed = [dict(r, removed_on=r["last_exit"]["date"], reason=r["last_exit"]["name"]) for r in rows
               if r["weight"] <= 0 and r["last_exit"] and since and r["last_exit"]["date"] > since]
    archived_since = utc_now() - timedelta(days=RECENT_DAYS)
    for item in session.scalars(select(PmItem).where(PmItem.user_id == principal.user.id,
                                                     PmItem.archived_at >= archived_since)):
        removed.append({"id": item.id, "symbol": item.symbol, "name": item.name, "removed_on": item.archived_at.date(),
                        "reason": "手动归档"})
    by_date = lambda r: str(r.get("activated_on") or r.get("removed_on") or "")  # noqa: E731
    session.commit()
    return safe({"latest": latest, "resonant": sorted(resonant, key=by_date, reverse=True),
                 "waiting": sorted(waiting, key=by_date, reverse=True),
                 "removed": sorted(removed, key=by_date, reverse=True)})


@router.get("/pm/lists/tops")
def tops(request: Request, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    """头部确立 (design §11.5): to watch, observing a top neckline, confirmed (with the verdict)."""
    results, _ = evaluate_user(session, market_of(request), principal.user.id)
    facts = [f for f in (top_facts(item, result) for item, result in results) if f is not None]
    confirmed = sorted([f for f in facts if f["state"] == "confirmed"], key=lambda f: str(f["confirmed_on"]),
                       reverse=True)
    verdicts = [f["verdict"] for f in confirmed if f["verdict"]]
    session.commit()
    return safe({"watching": [f for f in facts if f["state"] == "watching"],
                 "observing": [f for f in facts if f["state"] == "observing"], "confirmed": confirmed,
                 "accuracy": {"right": verdicts.count("判对"), "early": verdicts.count("判早"),
                              "neutral": verdicts.count("中性")}})


@router.get("/pm/campaigns")
def campaigns(request: Request, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    """模拟战役总账: every round's simulated trades; totals over the closed rounds."""
    rows = campaign_rows(session, market_of(request), principal.user.id)
    closed = [r for r in rows if not r["open"] and r["round_return"] is not None]
    returns = [r["round_return"] for r in closed]
    session.commit()
    return safe({"rows": rows, "totals": {
        "rounds": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
        "wins": sum(1 for x in returns if x > 0),
        "win_rate": sum(1 for x in returns if x > 0) / len(returns) if returns else None,
        "average": sum(returns) / len(returns) if returns else None}})
