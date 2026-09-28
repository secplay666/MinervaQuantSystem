"""/decisions and /intents: daily decisions, review, CSV export, forced runs."""

from __future__ import annotations

import csv
import io
import threading
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...decision.review import Operator, ReviewError, approve, approve_run, modify, override, reject
from ..db.base import utc_now
from ..db.models import Account, Approval, DecisionRun, Event, OrderIntent, RiskCheck, TargetPosition
from ..deps import Principal, api_error, get_session, require, settings_of
from ..services import account_costs

router = APIRouter(tags=["决策与审核"])
view = require("decision:view")
approver = require("decision:approve")
trigger = require("decision:trigger")


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ModifyIn(BaseModel):
    qty: int
    reason: str = Field(min_length=1, max_length=500)


class ApproveAllIn(BaseModel):
    include_warnings: bool = False


class TriggerIn(BaseModel):
    account_id: str
    reason: str = Field(min_length=2, max_length=500)
    rerun: bool = False


def operator(principal: Principal) -> Operator:
    return Operator(principal.actor, principal.user.id, principal.ip, principal.request_id)


def run_view(run: DecisionRun) -> dict:
    return {"run_id": run.run_id, "account_id": run.account_id, "trade_date": run.trade_date.isoformat(),
            "next_session": run.next_session.isoformat() if run.next_session else None, "kind": run.kind,
            "status": run.status, "gates": run.gates, "nav_fen": run.nav_fen, "cash_fen": run.cash_fen,
            "positions": run.positions, "summary": run.summary, "reason": run.reason,
            "strategy_id": run.strategy_id, "config_hash": run.config_hash, "data_version": run.data_version,
            "created_at": run.created_at.isoformat(), "created_by": run.created_by}


def intent_view(intent: OrderIntent, checks: list[RiskCheck], approvals: list[Approval]) -> dict:
    return {"intent_id": intent.intent_id, "run_id": intent.run_id, "seq": intent.seq, "symbol": intent.symbol,
            "side": intent.side, "qty": intent.qty, "proposed_qty": intent.proposed_qty,
            "ref_price_fen": intent.ref_price_fen, "limit_up_fen": intent.limit_up_fen,
            "limit_down_fen": intent.limit_down_fen, "est_notional_fen": intent.est_notional_fen,
            "est_fees_fen": intent.est_fees_fen, "reason": intent.reason, "rank": intent.rank, "risk": intent.risk,
            "status": intent.status, "execute_on": intent.execute_on.isoformat(),
            "valid_until": intent.valid_until.isoformat(),
            "checks": [{"rule_id": c.rule_id, "decision": c.decision, "message": c.message, "actual": c.actual,
                        "limit": c.limit_value} for c in checks],
            "history": [{"action": a.action, "qty_before": a.qty_before, "qty_after": a.qty_after,
                         "reason": a.reason, "actor": a.actor, "at": a.at.isoformat()} for a in approvals]}


def _review(session: Session, action) -> dict:
    try:
        intent = action()
    except ReviewError as exc:
        session.rollback()
        status = 404 if exc.code == "not_found" else 409 if exc.code in ("invalid_state", "expired",
                                                                         "already_filled") else 400
        raise api_error(status, exc.code, str(exc)) from None
    session.commit()
    checks = list(session.scalars(select(RiskCheck).where(RiskCheck.intent_id == intent.intent_id)))
    approvals = list(session.scalars(select(Approval).where(Approval.intent_id == intent.intent_id)
                                     .order_by(Approval.id)))
    return intent_view(intent, checks, approvals)


@router.get("/decisions")
def list_decisions(account_id: str | None = None, trade_date: date | None = None, include_superseded: bool = False,
                   limit: int = 30, _: Principal = Depends(view), session: Session = Depends(get_session)) -> list:
    query = select(DecisionRun)
    if not include_superseded:
        query = query.where(DecisionRun.status != "superseded")
    if account_id:
        query = query.where(DecisionRun.account_id == account_id)
    if trade_date:
        query = query.where(DecisionRun.trade_date == trade_date)
    runs = session.scalars(query.order_by(DecisionRun.trade_date.desc(), DecisionRun.created_at.desc())
                           .limit(min(limit, 200)))
    return [run_view(run) for run in runs]


@router.get("/decisions/{run_id}")
def get_decision(run_id: str, request: Request, _: Principal = Depends(view),
                 session: Session = Depends(get_session)) -> dict:
    run = session.get(DecisionRun, run_id)
    if run is None:
        raise api_error(404, "not_found", "决策不存在")
    intents = list(session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id).order_by(OrderIntent.seq)))
    checks = list(session.scalars(select(RiskCheck).where(RiskCheck.run_id == run_id)))
    approvals = list(session.scalars(select(Approval).where(Approval.intent_id.in_([i.intent_id for i in intents]))
                                     .order_by(Approval.id))) if intents else []
    targets = list(session.scalars(select(TargetPosition).where(TargetPosition.run_id == run_id)
                                   .order_by(TargetPosition.rank, TargetPosition.symbol)))
    events = session.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.event_id))
    market = request.app.state.market
    symbols = sorted({i.symbol for i in intents} | {t.symbol for t in targets})
    names = market.names(symbols) if symbols and market.available() else {}
    return {**run_view(run),
            "intents": [{**intent_view(i, [c for c in checks if c.intent_id == i.intent_id],
                                       [a for a in approvals if a.intent_id == i.intent_id]),
                         "name": names.get(i.symbol)} for i in intents],
            "run_checks": [{"rule_id": c.rule_id, "decision": c.decision, "message": c.message, "actual": c.actual,
                            "limit": c.limit_value} for c in checks if c.intent_id is None],
            "targets": [{"symbol": t.symbol, "name": names.get(t.symbol), "target_weight": t.target_weight,
                         "target_qty": t.target_qty, "rank": t.rank, "score": t.score, "explanation": t.explanation}
                        for t in targets],
            "events": [{"event_id": e.event_id, "level": e.level, "title": e.title, "body": e.body,
                        "symbol": e.symbol, "action_hint": e.action_hint} for e in events]}


@router.get("/decisions/{run_id}/export.csv")
def export_decision(run_id: str, _: Principal = Depends(view), session: Session = Depends(get_session)) -> Response:
    intents = session.scalars(select(OrderIntent).where(OrderIntent.run_id == run_id).order_by(OrderIntent.seq))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["序号", "代码", "方向", "数量", "参考价", "次日涨停价", "次日跌停价", "预计金额", "状态", "风险"])
    for i in intents:
        writer.writerow([i.seq, i.symbol, "买入" if i.side == "buy" else "卖出", i.qty, f"{i.ref_price_fen / 100:.2f}",
                         "" if i.limit_up_fen is None else f"{i.limit_up_fen / 100:.2f}",
                         "" if i.limit_down_fen is None else f"{i.limit_down_fen / 100:.2f}",
                         f"{i.est_notional_fen / 100:.2f}", i.status, i.risk])
    body = "\ufeff" + buffer.getvalue()  # BOM, so Excel opens the UTF-8 CSV correctly
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'})


@router.post("/intents/{intent_id}/approve")
def approve_intent(intent_id: str, body: ReasonIn, principal: Principal = Depends(approver),
                   session: Session = Depends(get_session)) -> dict:
    return _review(session, lambda: approve(session, intent_id, operator(principal), utc_now(), body.reason))


@router.post("/intents/{intent_id}/reject")
def reject_intent(intent_id: str, body: ReasonIn, principal: Principal = Depends(approver),
                  session: Session = Depends(get_session)) -> dict:
    return _review(session, lambda: reject(session, intent_id, operator(principal), utc_now(), body.reason or ""))


@router.post("/intents/{intent_id}/modify")
def modify_intent(intent_id: str, body: ModifyIn, request: Request, principal: Principal = Depends(approver),
                  session: Session = Depends(get_session)) -> dict:
    intent = session.get(OrderIntent, intent_id)
    if intent is None:
        raise api_error(404, "not_found", "交易意图不存在")
    account = session.get(Account, intent.account_id)
    fees, rules = account_costs(settings_of(request).root, account)
    security = request.app.state.market.security(intent.symbol) or {}
    lot = rules.lot_rule(security.get("board") or "SSE_MAIN")
    return _review(session, lambda: modify(session, intent_id, body.qty, operator(principal), utc_now(),
                                           body.reason, lot, fees, security.get("exchange") or "SSE"))


@router.post("/intents/{intent_id}/override")
def override_intent(intent_id: str, body: ReasonIn, principal: Principal = Depends(trigger),
                    session: Session = Depends(get_session)) -> dict:
    return _review(session, lambda: override(session, intent_id, operator(principal), utc_now(), body.reason or ""))


@router.post("/decisions/{run_id}/approve-all")
def approve_all(run_id: str, body: ApproveAllIn, principal: Principal = Depends(approver),
                session: Session = Depends(get_session)) -> dict:
    try:
        count = approve_run(session, run_id, operator(principal), utc_now(), body.include_warnings)
    except ReviewError as exc:
        session.rollback()
        raise api_error(404 if exc.code == "not_found" else 409, exc.code, str(exc)) from None
    session.commit()
    return {"approved": count}


@router.post("/decisions/trigger", status_code=202)
def trigger_decision(body: TriggerIn, request: Request, principal: Principal = Depends(trigger),
                     session: Session = Depends(get_session)) -> dict:
    """Forced rebalance for one account; runs in the background (tens of seconds)."""
    if session.get(Account, body.account_id) is None:
        raise api_error(404, "not_found", "账户不存在")
    settings = settings_of(request)
    jobs: dict = request.app.state.jobs
    if any(job["status"] == "running" for job in jobs.values()):
        raise api_error(409, "busy", "已有决策任务在运行")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "status": "running", "account_id": body.account_id, "started_at":
                    utc_now().isoformat(), "actor": principal.actor, "result": None}

    def work() -> None:
        from ...decision.job import run_daily

        try:
            outcomes = run_daily(settings.root, db_path=settings.db_path, account_ids=[body.account_id],
                                 force_reason=body.reason, rerun=body.rerun, actor=principal.actor)
            jobs[job_id].update(status="done", result=[vars(o) for o in outcomes])
        except Exception as exc:  # reported through the job status
            jobs[job_id].update(status="failed", result=f"{type(exc).__name__}: {exc}")
        jobs[job_id]["finished_at"] = utc_now().isoformat()

    threading.Thread(target=work, name=f"decision-{job_id}", daemon=True).start()
    return jobs[job_id]


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request, _: Principal = Depends(view)) -> dict:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise api_error(404, "not_found", "任务不存在")
    return job
