"""Position manager API, part 2: one item of the library and the chart workstation.

Labels are kept over time (each change has an effective session); levels are
confirmed versions (app/position.py).  Every change is audited.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db.base import utc_now
from ..db.models import PmItem, PmSentinel
from ..deps import Principal, api_error, get_session
from ..position import (
    LABELS, LEVEL_KINDS, PositionError, add_sentinel, confirm_level, evaluate, event_row, instrument_kind,
    item_detail, level_row, load_series, reset_sentinel, sentinel_row, set_label, shown_view, stage_params,
    user_settings,
)
from ...position.stages import stage_view
from .pm import clean, market_of, own_item, safe, use

router = APIRouter(tags=["仓位管家"])


class LabelChoice(BaseModel):
    """Either a label of the user's own, or ``use_view`` to adopt the system's stage view."""
    label: str | None = None
    use_view: bool = False


def detail(session: Session, request: Request, item: PmItem) -> dict:
    settings = user_settings(session, item.user_id)
    result = evaluate(session, market_of(request), item, settings)
    return clean([item_detail(session, item, result, settings)])[0]


@router.get("/pm/items/{item_id}")
def get_item(item_id: int, request: Request, principal: Principal = Depends(use),
             session: Session = Depends(get_session)) -> dict:
    item = own_item(session, principal, item_id)
    out = detail(session, request, item)
    session.commit()  # the settings row of a first visit
    return out


@router.put("/pm/items/{item_id}/label")
def put_label(item_id: int, body: LabelChoice, request: Request, principal: Principal = Depends(use),
              session: Session = Depends(get_session)) -> dict:
    item = own_item(session, principal, item_id)
    settings = user_settings(session, principal.user.id)
    series = load_series(market_of(request), item.symbol, item.kind)
    effective = series.latest or date.today()
    reason = None
    if body.use_view:
        if settings.label_mode != "suggest":
            raise api_error(400, "manual_mode", "手工模式下不提供系统观点，请直接选择标签")
        view = shown_view(stage_view(series.close, stage_params(settings), is_index=item.kind == "index"), series) \
            if series.dates else None
        if view is None:
            raise api_error(400, "no_data", "没有行情数据，无法给出阶段观点")
        label, source, reason = view["label"], "system", f"{view['name']}：{view['reason']}"
    elif body.label in LABELS:
        label, source = body.label, "manual"
    else:
        raise api_error(400, "invalid", "请选择右侧、顶部、筑底、左侧或未定")
    before = {"label": item.label, "source": item.label_source}
    set_label(session, item, label, source=source, actor=principal.actor, effective=effective, reason=reason)
    item.updated_at = utc_now()
    audit(session, principal.actor, "pm.label", "pm_item", str(item.id), before=before,
          after={"label": label, "source": source, "effective_date": effective.isoformat()},
          **principal.audit_kwargs())
    out = detail(session, request, item)
    session.commit()
    return out


class LevelEntry(BaseModel):
    kind: str                                  # neckline | target | top_neckline | base_zone | buyback_zone | reference
    price: float = Field(gt=0)                 # on the chart's scale, see basis
    lower: float | None = Field(default=None, gt=0)  # a zone's lower edge (base_zone, buyback_zone)
    fraction: float | None = Field(default=None, gt=0, le=1)  # buyback_zone: the share bought back
    basis: str = "qfq"                         # qfq | none | hfq (indices and ETFs are not adjusted)
    source: str = "manual"                     # manual | drawing | pattern
    new_round: bool = False                    # the first level of a new structure
    top_mode: str | None = None                # top_neckline: observe | confirmed
    effective_date: date | None = None         # default: the latest session
    note: str | None = Field(default=None, max_length=500)


@router.post("/pm/items/{item_id}/levels")
def post_level(item_id: int, body: LevelEntry, request: Request, principal: Principal = Depends(use),
               session: Session = Depends(get_session)) -> dict:
    item = own_item(session, principal, item_id)
    if body.kind not in LEVEL_KINDS or body.source not in ("manual", "drawing", "pattern"):
        raise api_error(400, "invalid", "未知的价位类型或来源")
    series = load_series(market_of(request), item.symbol, item.kind)
    try:
        level = confirm_level(session, item, series, body.kind, body.price, body.basis, actor=principal.actor,
                              source=body.source, lower=body.lower, new_round=body.new_round,
                              top_mode=body.top_mode, effective=body.effective_date, note=body.note,
                              fraction=body.fraction)
    except PositionError as exc:
        raise api_error(400, "invalid", str(exc)) from None
    item.updated_at = utc_now()
    audit(session, principal.actor, "pm.level", "pm_item", str(item.id), after=safe(level_row(level, series)),
          **principal.audit_kwargs())
    out = detail(session, request, item)
    session.commit()
    return out


@router.get("/pm/chart/{symbol}")
def chart_info(symbol: str, request: Request, principal: Principal = Depends(use),
               session: Session = Depends(get_session)) -> dict:
    """What the workstation draws for a code: the stage view, and for a library
    item its label, levels and rule events (flags on the chart)."""
    market = market_of(request)
    settings = user_settings(session, principal.user.id)
    suggest = settings.label_mode == "suggest"
    item = session.scalar(select(PmItem).where(PmItem.user_id == principal.user.id, PmItem.symbol == symbol,
                                               PmItem.archived_at.is_(None)))
    if item is None:
        try:
            kind, _ = instrument_kind(market, symbol)
        except PositionError as exc:
            raise api_error(404, "not_found", str(exc)) from None
        series = load_series(market, symbol, kind)
        view = stage_view(series.close, stage_params(settings), is_index=kind == "index") \
            if suggest and series.dates else None
        out = {"item_id": None, "symbol": symbol, "kind": kind, "stage": shown_view(view, series),
               "label": None, "levels": [], "events": [], "phase": None, "completion": None, "sentinels": []}
    else:
        result = evaluate(session, market, item, settings)
        series, outcome = result["series"], result["outcome"]
        events = (outcome.events if outcome else []) + result["prompts"]
        out = {"item_id": item.id, "symbol": symbol, "kind": item.kind,
               "stage": shown_view(result["view"], series) if suggest else None,
               "label": item.label, "levels": [level_row(lv, series) for lv in result["levels"]],
               "events": [event_row(e, series) for e in events if e.rule != "activated"],
               "phase": outcome.phase if outcome else None, "completion": outcome.completion if outcome else None,
               "sentinels": [sentinel_row(r, result) for r in result["sentinels"]]}
    session.commit()
    return safe(out)


class SentinelIn(BaseModel):
    price: float = Field(gt=0)            # on the chart's scale, see basis
    direction: str | None = None          # up | down; required for a new sentinel
    basis: str = "qfq"
    source_ref: str | None = Field(default=None, max_length=24)  # 颈线 / 起涨区上沿 / 前高 / 失效线 / 手工
    note: str | None = Field(default=None, max_length=500)


def own_sentinel(session: Session, principal: Principal, sentinel_id: int) -> tuple[PmSentinel, PmItem]:
    sentinel = session.get(PmSentinel, sentinel_id)
    item = session.get(PmItem, sentinel.item_id) if sentinel is not None else None
    if sentinel is None or sentinel.status == "removed" or item is None or item.user_id != principal.user.id:
        raise api_error(404, "not_found", "哨兵不存在")
    return sentinel, item


@router.post("/pm/items/{item_id}/sentinels")
def post_sentinel(item_id: int, body: SentinelIn, request: Request, principal: Principal = Depends(use),
                  session: Session = Depends(get_session)) -> dict:
    """A sentinel (design §11.3): reminds when a close goes through it, never changes the label."""
    item = own_item(session, principal, item_id)
    series = load_series(market_of(request), item.symbol, item.kind)
    try:
        sentinel = add_sentinel(session, item, series, body.price, body.direction or "", body.basis,
                                actor=principal.actor, source_ref=body.source_ref, note=body.note)
    except PositionError as exc:
        raise api_error(400, "invalid", str(exc)) from None
    audit(session, principal.actor, "pm.sentinel", "pm_item", str(item.id),
          after={"id": sentinel.id, "price": body.price, "direction": sentinel.direction, "basis": body.basis},
          **principal.audit_kwargs())
    out = detail(session, request, item)
    session.commit()
    return out


@router.patch("/pm/sentinels/{sentinel_id}")
def patch_sentinel(sentinel_id: int, body: SentinelIn, request: Request, principal: Principal = Depends(use),
                   session: Session = Depends(get_session)) -> dict:
    """A new price (dragged on the chart, or set again after a crossing)."""
    sentinel, item = own_sentinel(session, principal, sentinel_id)
    series = load_series(market_of(request), item.symbol, item.kind)
    before = {"price": sentinel.entered_price, "direction": sentinel.direction, "status": sentinel.status}
    try:
        reset_sentinel(session, sentinel, series, body.price, body.basis, body.direction)
    except PositionError as exc:
        raise api_error(400, "invalid", str(exc)) from None
    if body.source_ref is not None:
        sentinel.source_ref = body.source_ref or None
    if body.note is not None:
        sentinel.note = body.note or None
    audit(session, principal.actor, "pm.sentinel", "pm_item", str(item.id), before=before,
          after={"id": sentinel.id, "price": body.price, "direction": sentinel.direction, "version": sentinel.version},
          **principal.audit_kwargs())
    out = detail(session, request, item)
    session.commit()
    return out


@router.delete("/pm/sentinels/{sentinel_id}")
def delete_sentinel(sentinel_id: int, request: Request, principal: Principal = Depends(use),
                    session: Session = Depends(get_session)) -> dict:
    sentinel, item = own_sentinel(session, principal, sentinel_id)
    sentinel.status, sentinel.updated_at = "removed", utc_now()
    audit(session, principal.actor, "pm.sentinel.remove", "pm_item", str(item.id),
          before={"id": sentinel.id, "price": sentinel.entered_price, "direction": sentinel.direction},
          **principal.audit_kwargs())
    out = detail(session, request, item)
    session.commit()
    return out
