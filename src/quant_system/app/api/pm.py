"""Position manager API, part 1: shared helpers and the library (docs/design/position-manager.md §8).

Every route acts on the signed-in user's own items.  Prices go in and out
forward-adjusted (qfq) unless a basis says otherwise; the rules work in hfq
(app/position.py).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..db.models import PmItem
from ..deps import Principal, api_error, get_session, require
from ..market import records
from ..position import PositionError, evaluate_user, instrument_kind, summary_row

router = APIRouter(tags=["仓位管家"])
use = require("position:use")
CODE = re.compile(r"(?i)\b(?:(sh|sz|bj)?(\d{6})(?:\.(?:sh|sz|bj))?|(H\d{5}))\b")


def market_of(request: Request):
    market = request.app.state.market
    if not market.available():
        raise api_error(503, "no_market_data", "行情库不可用")
    return market


def own_item(session: Session, principal: Principal, item_id: int) -> PmItem:
    item = session.get(PmItem, item_id)
    if item is None or item.user_id != principal.user.id:
        raise api_error(404, "not_found", "标的不存在")
    return item


def clean(rows: list[dict]) -> list[dict]:
    """JSON-safe values (dates as ISO strings, NaN as null)."""
    import pandas as pd

    return records(pd.DataFrame(rows)) if rows else []


def parse_codes(text: str) -> list[str]:
    """600000, 600000.SH, sh600000 -> 600000; sh000300 and sz399006 (indices) keep the prefix."""
    out: list[str] = []
    for prefix, digits, csindex in CODE.findall(text):
        if csindex:
            code = csindex.upper()
        elif prefix and prefix.lower() in ("sh", "sz") and digits.startswith(("000", "399")) \
                and not (prefix.lower() == "sz" and digits.startswith("000")):
            code = f"{prefix.lower()}{digits}"
        else:
            code = digits
        if code not in out:
            out.append(code)
    return out


class ItemsIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)  # codes separated by spaces, commas or lines
    group: str | None = Field(default=None, max_length=32)


class ItemPatch(BaseModel):
    groups: list[str] | None = None
    star: int | None = Field(default=None, ge=0, le=3)
    note: str | None = Field(default=None, max_length=500)
    primary_index: str | None = Field(default=None, max_length=16)
    archived: bool | None = None


@router.get("/pm/items")
def list_items(request: Request, principal: Principal = Depends(use), session: Session = Depends(get_session)) -> dict:
    results, settings = evaluate_user(session, market_of(request), principal.user.id)
    rows = [summary_row(item, result, settings) for item, result in results]
    session.commit()
    return {"items": clean(rows), "groups": sorted({g for r in rows for g in r["groups"]}),
            "label_mode": settings.label_mode}


@router.post("/pm/items", status_code=201)
def add_items(body: ItemsIn, request: Request, principal: Principal = Depends(use),
              session: Session = Depends(get_session)) -> dict:
    market = market_of(request)
    added, skipped, errors = [], [], []
    for code in parse_codes(body.text)[:500]:
        existing = session.scalar(select(PmItem).where(PmItem.user_id == principal.user.id, PmItem.symbol == code))
        if existing is not None:
            (added if existing.archived_at is not None else skipped).append(code)
            existing.archived_at = None
            if body.group and body.group not in (existing.groups or []):
                existing.groups = [*(existing.groups or []), body.group]
            continue
        try:
            kind, name = instrument_kind(market, code)
        except PositionError as exc:
            errors.append(str(exc))
            continue
        session.add(PmItem(user_id=principal.user.id, symbol=code, kind=kind, name=name,
                           groups=[body.group] if body.group else [], star=0, label="undecided"))
        added.append(code)
    session.commit()
    return {"added": added, "skipped": skipped, "errors": errors}


@router.patch("/pm/items/{item_id}")
def patch_item(item_id: int, body: ItemPatch, principal: Principal = Depends(use),
               session: Session = Depends(get_session)) -> dict:
    item = own_item(session, principal, item_id)
    if body.groups is not None:
        item.groups = sorted({g.strip()[:32] for g in body.groups if g.strip()})
    if body.star is not None:
        item.star = body.star
    if body.note is not None:
        item.note = body.note or None
    if body.primary_index is not None:
        if body.primary_index and not re.fullmatch(r"(sh|sz)\d{6}", body.primary_index):
            raise api_error(400, "invalid", "主指数须是指数代码，如 sh000300")
        item.primary_index = body.primary_index or None
    if body.archived is not None:
        item.archived_at = utc_now() if body.archived else None
    item.updated_at = utc_now()
    session.commit()
    return {"id": item.id}
