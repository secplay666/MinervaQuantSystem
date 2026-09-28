"""/accounts: accounts, holdings, NAV history, ledger events, fills and
holdings entry (ADR-008 §9)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...decision.accounts import create_account, replay, reverse_event
from ...decision.review import (
    Operator,
    ReviewError,
    commit_holdings,
    parse_holdings_text,
    preview_holdings,
    record_fill,
    refresh_intent_fill,
)
from ...domain.money import yuan_to_fen
from ..audit import audit
from ..db.base import utc_now
from ..db.models import Account, AccountSnapshot, Fill, PositionEvent, PositionSnapshot
from ..deps import Principal, api_error, get_session, require, settings_of
from ..services import account_costs

router = APIRouter(tags=["账户"])
view = require("account:view")
edit = require("account:edit")
manage = require("account:manage")


class AccountCreate(BaseModel):
    account_id: str = Field(pattern=r"^[A-Za-z0-9_-]{2,32}$")
    name: str = Field(min_length=1, max_length=128)
    mode: str = Field(pattern=r"^(paper|manual)$")
    cash: str = Field(description="initial cash in CNY")
    strategy_config: str = "configs/strategies/multifactor_rules.json"
    start_date: date | None = None
    note: str | None = None


class AccountPatch(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    note: str | None = None


class FillIn(BaseModel):
    intent_id: str | None = None
    trade_date: date
    symbol: str = Field(pattern=r"^\d{6}$")
    side: str = Field(pattern=r"^(buy|sell)$")
    qty: int = Field(gt=0)
    price: str = Field(description="average fill price in CNY")
    commission: str | None = None
    stamp_duty: str | None = None
    transfer_fee: str | None = None


class HoldingRow(BaseModel):
    symbol: str = Field(pattern=r"^\d{6}$")
    qty: int = Field(ge=0)
    cost_price: float | None = None


class HoldingsPreviewIn(BaseModel):
    as_of: date
    cash: str
    rows: list[HoldingRow] | None = None
    text: str | None = Field(default=None, description="pasted rows: code, qty[, cost price]")
    filename: str | None = None


class HoldingsCommitIn(BaseModel):
    batch_id: str
    reason: str = Field(min_length=1, max_length=500)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    trade_date: date | None = None


def _fen(value: str | None, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return yuan_to_fen(Decimal(value))
    except (InvalidOperation, ValueError):
        raise api_error(400, "invalid_number", f"{field} 不是合法金额") from None


def _account(session: Session, account_id: str) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise api_error(404, "not_found", "账户不存在")
    return account


def _operator(principal: Principal) -> Operator:
    return Operator(principal.actor, principal.user.id, principal.ip, principal.request_id)


def account_view(session: Session, account: Account) -> dict:
    snapshot = session.scalars(select(AccountSnapshot).where(AccountSnapshot.account_id == account.account_id)
                               .order_by(AccountSnapshot.trade_date.desc())).first()
    return {"account_id": account.account_id, "name": account.name, "mode": account.mode,
            "strategy_config": account.strategy_config, "initial_cash_fen": account.initial_cash_fen,
            "start_date": account.start_date.isoformat(), "is_active": account.is_active, "note": account.note,
            "holdings_confirmed_date": account.holdings_confirmed_date.isoformat()
            if account.holdings_confirmed_date else None,
            "latest": None if snapshot is None else {
                "trade_date": snapshot.trade_date.isoformat(), "nav_fen": snapshot.nav_fen,
                "cash_fen": snapshot.cash_fen, "market_value_fen": snapshot.market_value_fen,
                "positions": snapshot.positions}}


def _review(session: Session, action):
    try:
        result = action()
    except ReviewError as exc:
        session.rollback()
        status = 404 if exc.code == "not_found" else 409 if exc.code in ("invalid_state", "stale_preview") else 400
        raise api_error(status, exc.code, str(exc)) from None
    except ValueError as exc:
        session.rollback()
        raise api_error(400, "invalid", str(exc)) from None
    session.commit()
    return result


@router.get("/accounts")
def list_accounts(_: Principal = Depends(view), session: Session = Depends(get_session)) -> list[dict]:
    return [account_view(session, a) for a in session.scalars(select(Account).order_by(Account.account_id))]


@router.post("/accounts", status_code=201)
def new_account(body: AccountCreate, request: Request, principal: Principal = Depends(manage),
                session: Session = Depends(get_session)) -> dict:
    if session.get(Account, body.account_id) is not None:
        raise api_error(409, "exists", "账户编号已存在")
    cash = _fen(body.cash, "初始资金")
    account = _review(session, lambda: create_account(
        session, settings_of(request).root, account_id=body.account_id, name=body.name, mode=body.mode,
        strategy_config=body.strategy_config, initial_cash_fen=cash or 0,
        start_date=body.start_date or date.today(), actor=principal.actor, note=body.note))
    return account_view(session, account)


@router.patch("/accounts/{account_id}")
def patch_account(account_id: str, body: AccountPatch, principal: Principal = Depends(manage),
                  session: Session = Depends(get_session)) -> dict:
    account = _account(session, account_id)
    before = account_view(session, account)
    for field in ("name", "is_active", "note"):
        value = getattr(body, field)
        if value is not None:
            setattr(account, field, value)
    session.flush()
    after = account_view(session, account)
    audit(session, principal.actor, "account.update", "account", account_id, before=before, after=after,
          **principal.audit_kwargs())
    session.commit()
    return after


@router.get("/accounts/{account_id}")
def get_account(account_id: str, request: Request, _: Principal = Depends(view),
                session: Session = Depends(get_session)) -> dict:
    account = _account(session, account_id)
    ledger = replay(session, account_id)
    market = request.app.state.market
    closes = market.latest_closes(sorted(ledger.positions)) if ledger.positions and market.available() else {}
    holdings, value = [], 0
    for symbol, position in sorted(ledger.positions.items()):
        close = closes.get(symbol)
        mv = int(round(close * 100)) * position.quantity if close is not None else None
        value += mv or 0
        holdings.append({"symbol": symbol, "qty": position.quantity, "sellable": position.sellable,
                         "cost_fen": position.cost_fen, "close": close, "market_value_fen": mv,
                         "pnl_fen": None if mv is None else mv - position.cost_fen})
    names = market.names(sorted(ledger.positions)) if ledger.positions and market.available() else {}
    for row in holdings:
        row["name"] = names.get(row["symbol"])
    total = ledger.cash_fen + value
    for row in holdings:
        row["weight"] = (row["market_value_fen"] or 0) / total if total else None
    return {**account_view(session, account), "cash_fen": ledger.cash_fen, "market_value_fen": value,
            "nav_fen": total, "holdings": holdings}


@router.get("/accounts/{account_id}/nav")
def account_nav(account_id: str, _: Principal = Depends(view), session: Session = Depends(get_session)) -> list:
    _account(session, account_id)
    rows = session.scalars(select(AccountSnapshot).where(AccountSnapshot.account_id == account_id)
                           .order_by(AccountSnapshot.trade_date))
    return [{"trade_date": r.trade_date.isoformat(), "nav_fen": r.nav_fen, "cash_fen": r.cash_fen,
             "market_value_fen": r.market_value_fen, "positions": r.positions} for r in rows]


@router.get("/accounts/{account_id}/positions")
def account_positions(account_id: str, trade_date: date | None = None, _: Principal = Depends(view),
                      session: Session = Depends(get_session)) -> dict:
    _account(session, account_id)
    if trade_date is None:
        trade_date = session.scalar(select(PositionSnapshot.trade_date).where(
            PositionSnapshot.account_id == account_id).order_by(PositionSnapshot.trade_date.desc()).limit(1))
    rows = session.scalars(select(PositionSnapshot).where(PositionSnapshot.account_id == account_id,
                                                          PositionSnapshot.trade_date == trade_date)
                           .order_by(PositionSnapshot.symbol)) if trade_date else []
    return {"trade_date": trade_date.isoformat() if trade_date else None,
            "positions": [{"symbol": r.symbol, "qty": r.qty, "mark_fen": r.mark_fen, "value_fen": r.value_fen,
                           "cost_fen": r.cost_fen} for r in rows]}


@router.get("/accounts/{account_id}/events")
def account_events(account_id: str, limit: int = 200, _: Principal = Depends(view),
                   session: Session = Depends(get_session)) -> list:
    _account(session, account_id)
    rows = session.scalars(select(PositionEvent).where(PositionEvent.account_id == account_id)
                           .order_by(PositionEvent.seq.desc()).limit(min(limit, 1000)))
    return [{"event_id": r.event_id, "trade_date": r.trade_date.isoformat(), "kind": r.kind, "symbol": r.symbol,
             "payload": r.payload, "ref_id": r.ref_id, "reason": r.reason, "created_by": r.created_by,
             "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/accounts/{account_id}/fills")
def account_fills(account_id: str, limit: int = 200, _: Principal = Depends(view),
                  session: Session = Depends(get_session)) -> list:
    _account(session, account_id)
    rows = session.scalars(select(Fill).where(Fill.account_id == account_id)
                           .order_by(Fill.trade_date.desc(), Fill.fill_id.desc()).limit(min(limit, 1000)))
    return [{"fill_id": r.fill_id, "intent_id": r.intent_id, "trade_date": r.trade_date.isoformat(),
             "symbol": r.symbol, "side": r.side, "qty": r.qty, "price_fen": r.price_fen,
             "fees_fen": r.commission_fen + r.stamp_duty_fen + r.transfer_fee_fen,
             "fees_estimated": r.fees_estimated, "source": r.source, "created_by": r.created_by} for r in rows]


@router.post("/accounts/{account_id}/fills", status_code=201)
def add_fill(account_id: str, body: FillIn, request: Request, principal: Principal = Depends(edit),
             session: Session = Depends(get_session)) -> dict:
    account = _account(session, account_id)
    fees, _rules = account_costs(settings_of(request).root, account)
    security = request.app.state.market.security(body.symbol) if request.app.state.market.available() else None
    if security is None:
        raise api_error(400, "unknown_symbol", f"{body.symbol} 不在证券主数据中")
    price = _fen(body.price, "成交价")
    fill = _review(session, lambda: record_fill(
        session, account, trade_date=body.trade_date, symbol=body.symbol, side=body.side, qty=body.qty,
        price_fen=price or 0, op=_operator(principal), fees=fees, exchange=security["exchange"],
        intent_id=body.intent_id, commission_fen=_fen(body.commission, "佣金"),
        stamp_duty_fen=_fen(body.stamp_duty, "印花税"), transfer_fee_fen=_fen(body.transfer_fee, "过户费")))
    return {"fill_id": fill.fill_id, "fees_fen": fill.commission_fen + fill.stamp_duty_fen + fill.transfer_fee_fen,
            "fees_estimated": fill.fees_estimated}


@router.post("/accounts/{account_id}/holdings/preview")
def holdings_preview(account_id: str, body: HoldingsPreviewIn, request: Request,
                     principal: Principal = Depends(edit), session: Session = Depends(get_session)) -> dict:
    account = _account(session, account_id)
    rows = [row.model_dump() for row in body.rows or []]
    errors: list[str] = []
    if body.text:
        parsed, errors = parse_holdings_text(body.text)
        rows += parsed
    market = request.app.state.market
    known = market.known_symbols() if market.available() else {r["symbol"] for r in rows}
    cash = _fen(body.cash, "现金")
    batch = _review(session, lambda: preview_holdings(session, account, as_of=body.as_of, cash_fen=cash or 0,
                                                      rows=rows, known_symbols=known, op=_operator(principal),
                                                      filename=body.filename))
    summary = dict(batch.summary or {})
    summary["errors"] = errors + summary.get("errors", [])
    return {"batch_id": batch.batch_id, **summary}


@router.post("/accounts/{account_id}/holdings/commit")
def holdings_commit(account_id: str, body: HoldingsCommitIn, principal: Principal = Depends(edit),
                    session: Session = Depends(get_session)) -> dict:
    account = _account(session, account_id)
    batch = _review(session, lambda: commit_holdings(session, account, body.batch_id, _operator(principal),
                                                     body.reason))
    return {"batch_id": batch.batch_id, "status": batch.status}


@router.post("/position-events/{event_id}/reverse")
def reverse(event_id: str, body: ReverseIn, principal: Principal = Depends(edit),
            session: Session = Depends(get_session)) -> dict:
    row = session.scalar(select(PositionEvent).where(PositionEvent.event_id == event_id))
    if row is None:
        raise api_error(404, "not_found", "记录不存在")
    reversal = _review(session, lambda: reverse_event(session, row.account_id, event_id,
                                                      trade_date=body.trade_date or row.trade_date,
                                                      actor=principal.actor, reason=body.reason))
    if row.kind == "fill" and row.ref_id:
        fill = session.get(Fill, row.ref_id)
        if fill is not None:
            fill.reversed_by = reversal.event_id
            session.flush()
            if fill.intent_id:
                refresh_intent_fill(session, fill.intent_id)
            session.commit()
    return {"event_id": reversal.event_id, "reverses": event_id, "at": utc_now().isoformat()}
