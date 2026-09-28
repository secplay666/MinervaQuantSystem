"""Human review and account facts (ADR-008 §6–§9): approve / modify / reject /
override intents, record fills, enter holdings.  The API only authenticates
and validates input; the rules live here.

Every change is audited with the operator, before/after and reason.  Ledger
writes are validated by replaying the account with the new event first, so a
fill selling more than is held, or a holdings entry that would make cash
negative, is refused instead of stored.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..app.audit import audit
from ..app.db.base import utc_now
from ..app.db.models import (
    Account,
    Approval,
    DecisionRun,
    Fill,
    ImportBatch,
    OrderIntent,
    PositionEvent,
    RiskCheck,
)
from ..data_platform.sessions import SHANGHAI_TZ
from ..domain.fees import FeeSchedule
from ..domain.rules import LotRule
from ..ledger import LedgerInvariantError
from .accounts import account_events, replay, replay_rows, reverse_event
from .intents import SEVERITY, Check, cash_check, quantity_checks
from .paper import approved_at, cutoff

REVIEWABLE = ("pending_approval",)
EXECUTABLE = ("approved", "modified")
OPEN_FOR_CASH = ("pending_approval", "approved", "modified")  # the intents R6 counts
CLOSED_EXECUTION = ("unfilled", "partial_closed")  # paper gave up: reversing a fill does not reopen them


class ReviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Operator:
    actor: str
    user_id: int | None = None
    ip: str | None = None
    request_id: str | None = None

    def audit_kwargs(self) -> dict:
        return {"user_id": self.user_id, "ip": self.ip, "request_id": self.request_id}


def _intent(session: Session, intent_id: str) -> OrderIntent:
    intent = session.get(OrderIntent, intent_id)
    if intent is None:
        raise ReviewError("not_found", f"交易意图 {intent_id} 不存在")
    return intent


def _open(intent: OrderIntent, now: datetime) -> None:
    if now > intent.valid_until:
        raise ReviewError("expired", "已过审核截止时间")


def _snapshot(intent: OrderIntent) -> dict:
    return {"status": intent.status, "qty": intent.qty}


def _today(now: datetime) -> date:
    return now.astimezone(SHANGHAI_TZ).date()


def _last_cutoff_day(now: datetime, sessions: Sequence[date] | None = None) -> date:
    """The latest session whose 09:15 cutoff has passed, from the exchange
    calendar; without one, weekdays stand in for sessions (a holiday then
    locks a paper intent until the next real session is booked)."""
    today = _today(now)
    if sessions:
        k = bisect_right(sessions, today)
        if k and now < cutoff(sessions[k - 1]):
            k -= 1
        return sessions[k - 1] if k else date.min
    day = today if now >= cutoff(today) else today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def paper_locked(session: Session, intent: OrderIntent, now: datetime,
                 sessions: Sequence[date] | None = None) -> bool:
    """An executable paper intent is locked from the cutoff of the session it
    trades in until the paper job has booked that session: changing it in
    between would decide with knowledge of the open (ADR-008 §6)."""
    account = session.get(Account, intent.account_id)
    if account is None or account.mode != "paper" or intent.status not in EXECUTABLE:
        return False
    day = _last_cutoff_day(now, sessions)
    approved = approved_at(session, intent.intent_id)
    if day < intent.execute_on or approved is None or approved > cutoff(day):
        return False
    return account.paper_through is None or account.paper_through < day


def _check_paper_lock(session: Session, intent: OrderIntent, now: datetime,
                      sessions: Sequence[date] | None = None) -> None:
    if paper_locked(session, intent, now, sessions):
        raise ReviewError("paper_locked", "模拟账户：该意图按当日开盘模拟成交，09:15 之后到晚间模拟成交入账之前不能再改")


def approve(session: Session, intent_id: str, op: Operator, now: datetime, reason: str | None = None) -> OrderIntent:
    intent = _intent(session, intent_id)
    if intent.status not in REVIEWABLE:
        raise ReviewError("invalid_state", f"当前状态 {intent.status} 不能批准")
    _open(intent, now)
    before = _snapshot(intent)
    intent.status = "approved"
    session.add(Approval(intent_id=intent_id, action="approve", qty_before=intent.qty, qty_after=intent.qty,
                         reason=reason, user_id=op.user_id, actor=op.actor, at=now))
    audit(session, op.actor, "intent.approve", "order_intent", intent_id, before=before, after=_snapshot(intent),
          reason=reason, **op.audit_kwargs())
    return intent


def reject(session: Session, intent_id: str, op: Operator, now: datetime, reason: str,
           sessions: Sequence[date] | None = None) -> OrderIntent:
    if not reason or not reason.strip():
        raise ReviewError("reason_required", "拒绝需要填写原因")
    intent = _intent(session, intent_id)
    if intent.status not in (*REVIEWABLE, *EXECUTABLE):
        raise ReviewError("invalid_state", f"当前状态 {intent.status} 不能拒绝")
    if intent.status in EXECUTABLE and _filled_qty(session, intent_id):
        raise ReviewError("already_filled", "已有成交回填，不能拒绝")
    _check_paper_lock(session, intent, now, sessions)
    before = _snapshot(intent)
    intent.status = "rejected"
    session.add(Approval(intent_id=intent_id, action="reject", qty_before=intent.qty, qty_after=intent.qty,
                         reason=reason, user_id=op.user_id, actor=op.actor, at=now))
    audit(session, op.actor, "intent.reject", "order_intent", intent_id, before=before, after=_snapshot(intent),
          reason=reason, **op.audit_kwargs())
    return intent


def modify(session: Session, intent_id: str, qty: int, op: Operator, now: datetime, reason: str, lot: LotRule,
           fees: FeeSchedule, exchange: str, sessions: Sequence[date] | None = None) -> OrderIntent:
    """Approve with a different quantity (lot-legal; a sell may not exceed the
    holding), after re-running the quantity-dependent checks (ADR-008 §7)."""
    if not reason or not reason.strip():
        raise ReviewError("reason_required", "修改数量需要填写原因")
    intent = _intent(session, intent_id)
    if intent.status not in (*REVIEWABLE, *EXECUTABLE):
        raise ReviewError("invalid_state", f"当前状态 {intent.status} 不能修改")
    _open(intent, now)
    if _filled_qty(session, intent_id):
        raise ReviewError("already_filled", "已有成交回填，不能修改数量")
    _check_paper_lock(session, intent, now, sessions)
    if qty <= 0:
        raise ReviewError("invalid_qty", "数量必须为正；不交易请用拒绝")
    if intent.side == "buy":
        if lot.round_buy(qty) != qty:
            raise ReviewError("invalid_qty", f"买入数量须为 {lot.buy_min} 股起、{lot.buy_step} 股整数倍")
    else:
        holding = replay(session, intent.account_id).quantity(intent.symbol)
        if qty > holding or lot.round_sell(qty, holding) != qty:
            raise ReviewError("invalid_qty", f"卖出数量不合法（持有 {holding} 股，零股只能一次卖完）")
    notional = qty * intent.ref_price_fen
    est_fees = fees.fees(intent.side, exchange, notional, intent.execute_on).total_fen
    checks = _recheck(session, intent, qty, notional, est_fees)
    before = _snapshot(intent)
    previous = intent.qty
    intent.qty = qty
    intent.est_notional_fen = notional
    intent.est_fees_fen = est_fees
    intent.status = "modified"
    if checks is not None:
        session.execute(delete(RiskCheck).where(RiskCheck.intent_id == intent_id,
                                                RiskCheck.rule_id.in_(("R4", "R5", "R6"))))
        for check in checks:
            session.add(RiskCheck(run_id=intent.run_id, intent_id=intent_id, rule_id=check.rule_id,
                                  decision=check.decision, actual=check.actual, limit_value=check.limit,
                                  message=check.message))
        session.flush()
        remaining = session.scalars(select(RiskCheck.decision).where(RiskCheck.intent_id == intent_id))
        intent.risk = max(remaining, key=SEVERITY.__getitem__, default="pass")
    session.add(Approval(intent_id=intent_id, action="modify", qty_before=previous, qty_after=qty, reason=reason,
                         user_id=op.user_id, actor=op.actor, at=now))
    audit(session, op.actor, "intent.modify", "order_intent", intent_id, before=before, after=_snapshot(intent),
          reason=reason, **op.audit_kwargs())
    return intent


def _recheck(session: Session, intent: OrderIntent, qty: int, notional: int, est_fees: int) -> list[Check] | None:
    """R4/R5 for the new quantity and R6 for the run with it.  A reject refuses
    an increase; a reduction never adds risk, so its rejects are kept as
    warnings.  Runs from before the limits were recorded may only be reduced
    (None: keep their checks)."""
    run = session.get(DecisionRun, intent.run_id)
    limits = (run.summary or {}).get("limits") if run else None
    if not limits:
        if qty > intent.proposed_qty:
            raise ReviewError("no_limits", "该决策没有记录风控参数，只能减少数量")
        return None
    held = replay(session, intent.account_id, through=intent.trade_date).quantity(intent.symbol)
    checks = quantity_checks(intent.side, qty, intent.ref_price_fen, held, run.nav_fen or 0,
                             (limits.get("volume_cap") or {}).get(intent.symbol),
                             float(limits.get("max_participation") or 0), limits.get("max_weight"))
    buys = proceeds = 0
    for other in session.scalars(select(OrderIntent).where(OrderIntent.run_id == intent.run_id,
                                                           OrderIntent.status.in_(OPEN_FOR_CASH))):
        this = other.intent_id == intent.intent_id
        n, f = (notional, est_fees) if this else (other.est_notional_fen, other.est_fees_fen)
        if other.side == "buy":
            buys += n + f
        else:
            proceeds += n - f
    cash = cash_check(buys, (run.cash_fen or 0) + proceeds)
    if cash.decision == "reject":
        # A smaller sell leaves the buys short: say so, but the sell itself is fine.
        checks.append(cash if intent.side == "buy" else replace(cash, decision="warn",
                                                                message="减少卖出后，本次买入金额加费用超过可用资金"))
    rejected = [c for c in checks if c.decision == "reject"]
    if rejected and qty > intent.qty:
        raise ReviewError("risk_reject", "修改后不通过风控：" + "；".join(f"{c.rule_id} {c.message}" for c in rejected))
    return [replace(c, decision="warn", message=f"{c.message}（减少数量后仍未通过，仅提示）")
            if c.decision == "reject" else c for c in checks]


def override(session: Session, intent_id: str, op: Operator, now: datetime, reason: str) -> OrderIntent:
    """Approve an intent the pre-trade checks rejected (explicit authorisation, ARCHITECTURE §5.7)."""
    if not reason or not reason.strip():
        raise ReviewError("reason_required", "覆盖风控需要填写原因")
    intent = _intent(session, intent_id)
    if intent.status != "rejected_by_risk":
        raise ReviewError("invalid_state", "只有被风控拒绝的意图可以覆盖")
    _open(intent, now)
    before = _snapshot(intent)
    intent.status = "approved"
    session.add(Approval(intent_id=intent_id, action="override", qty_before=intent.qty, qty_after=intent.qty,
                         reason=reason, user_id=op.user_id, actor=op.actor, at=now))
    audit(session, op.actor, "intent.override", "order_intent", intent_id, before=before, after=_snapshot(intent),
          reason=reason, **op.audit_kwargs())
    return intent


def approve_run(session: Session, run_id: str, op: Operator, now: datetime, include_warnings: bool) -> int:
    run = session.get(DecisionRun, run_id)
    if run is None:
        raise ReviewError("not_found", f"决策 {run_id} 不存在")
    query = select(OrderIntent).where(OrderIntent.run_id == run_id, OrderIntent.status.in_(REVIEWABLE))
    if not include_warnings:
        query = query.where(OrderIntent.risk == "pass")
    count = 0
    for intent in session.scalars(query.order_by(OrderIntent.seq)):
        approve(session, intent.intent_id, op, now, reason="批量批准")
        count += 1
    return count


# -- fills ---------------------------------------------------------------------------------


def _filled_qty(session: Session, intent_id: str) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(Fill.qty), 0)).where(
        Fill.intent_id == intent_id, Fill.reversed_by.is_(None))) or 0)


def refresh_intent_fill(session: Session, intent_id: str) -> None:
    """Recompute an intent's filled quantity from its fills that are not reversed."""
    intent = session.get(OrderIntent, intent_id)
    if intent is None:
        return
    intent.filled_qty = _filled_qty(session, intent_id)
    if intent.execution in CLOSED_EXECUTION:
        return
    intent.execution = ("filled" if intent.filled_qty >= intent.qty else "partial") if intent.filled_qty else None


def _validate(session: Session, account_id: str, new_rows: list[PositionEvent]) -> None:
    rows = sorted([*account_events(session, account_id), *new_rows], key=lambda r: (r.trade_date, r.seq or 10**12))
    try:
        replay_rows(account_id, rows)
    except LedgerInvariantError as exc:
        raise ReviewError("ledger", f"与账本不一致：{exc}") from exc


def record_fill(session: Session, account: Account, *, trade_date: date, symbol: str, side: str, qty: int,
                price_fen: int, op: Operator, fees: FeeSchedule, exchange: str, intent_id: str | None = None,
                commission_fen: int | None = None, stamp_duty_fen: int | None = None,
                transfer_fee_fen: int | None = None, batch_id: str | None = None,
                now: datetime | None = None) -> Fill:
    if account.mode != "manual":
        raise ReviewError("paper_account", "模拟账户的成交由系统模拟，不能手工回填")
    if side not in ("buy", "sell") or qty <= 0 or price_fen <= 0:
        raise ReviewError("invalid_fill", "方向、数量或价格不合法")
    if any(fee is not None and fee < 0 for fee in (commission_fen, stamp_duty_fen, transfer_fee_fen)):
        raise ReviewError("invalid_fill", "费用不能为负")
    _check_entry_date(account, trade_date, now or utc_now(), "成交")
    if intent_id is not None:
        intent = _intent(session, intent_id)
        if intent.account_id != account.account_id or intent.symbol != symbol or intent.side != side:
            raise ReviewError("mismatch", "成交与交易意图的账户、代码或方向不一致")
        if intent.status not in EXECUTABLE:
            raise ReviewError("invalid_state", "只有已批准的意图可以回填成交")
        if _filled_qty(session, intent_id) + qty > intent.qty:
            raise ReviewError("overfilled", "回填数量超过交易意图的数量")
    notional = qty * price_fen
    estimated = commission_fen is None and stamp_duty_fen is None and transfer_fee_fen is None
    if estimated:
        breakdown = fees.fees(side, exchange, notional, trade_date)
        commission_fen, stamp_duty_fen, transfer_fee_fen = (breakdown.commission_fen, breakdown.stamp_duty_fen,
                                                            breakdown.transfer_fee_fen)
    count = session.scalar(select(func.count()).select_from(Fill).where(Fill.account_id == account.account_id)) or 0
    fill_id = f"fill-{account.account_id}-{trade_date:%Y%m%d}-{count + 1:05d}"
    payload = {"side": side, "quantity": qty, "price_fen": price_fen, "notional_fen": notional,
               "commission_fen": int(commission_fen or 0), "stamp_duty_fen": int(stamp_duty_fen or 0),
               "transfer_fee_fen": int(transfer_fee_fen or 0)}
    event = PositionEvent(event_id=f"{fill_id}-event", account_id=account.account_id, trade_date=trade_date,
                          kind="fill", symbol=symbol, payload=payload, ref_id=fill_id, created_by=op.actor)
    _validate(session, account.account_id, [event])
    fill = Fill(fill_id=fill_id, account_id=account.account_id, intent_id=intent_id, trade_date=trade_date,
                symbol=symbol, side=side, qty=qty, price_fen=price_fen, commission_fen=payload["commission_fen"],
                stamp_duty_fen=payload["stamp_duty_fen"], transfer_fee_fen=payload["transfer_fee_fen"],
                fees_estimated=estimated, source="import" if batch_id else "manual", batch_id=batch_id,
                created_by=op.actor)
    session.add(fill)
    session.add(event)
    session.flush()
    if intent_id is not None:
        refresh_intent_fill(session, intent_id)
    audit(session, op.actor, "fill.record", "fill", fill_id, after={**payload, "symbol": symbol,
          "trade_date": trade_date.isoformat(), "intent_id": intent_id}, **op.audit_kwargs())
    return fill


def _check_entry_date(account: Account, day: date, now: datetime, what: str) -> None:
    """Facts are dated after the confirmed holdings (which already include
    that day) and not in the future."""
    if day > _today(now):
        raise ReviewError("future_date", f"{what}日期 {day} 晚于今天")
    confirmed = account.holdings_confirmed_date
    if confirmed is not None and day <= confirmed:
        raise ReviewError("before_confirmed", f"{what}日期 {day} 不晚于已确认持仓的日期 {confirmed}，"
                                              "已包含在确认的持仓里；如需更正请重新录入持仓")


def reverse(session: Session, account: Account, event_id: str, op: Operator, reason: str,
            trade_date: date | None = None, now: datetime | None = None) -> PositionEvent:
    """Reverse a manual account's ledger event; a reversed fill no longer
    counts towards its intent.  Paper accounts are maintained by the system."""
    if account.mode != "manual":
        raise ReviewError("paper_account", "模拟账户的记录由系统维护，不能冲销")
    row = session.scalar(select(PositionEvent).where(PositionEvent.event_id == event_id,
                                                     PositionEvent.account_id == account.account_id))
    if row is None:
        raise ReviewError("not_found", "记录不存在")
    now = now or utc_now()
    effective = trade_date or row.trade_date
    _check_entry_date(account, row.trade_date, now, "被冲销记录的")
    if effective < row.trade_date:
        raise ReviewError("invalid_date", "冲销日期不能早于原记录的日期")
    _check_entry_date(account, effective, now, "冲销")
    try:
        reversal = reverse_event(session, account.account_id, event_id, trade_date=effective,
                                 actor=op.actor, reason=reason)
    except ValueError as exc:
        raise ReviewError("ledger", str(exc)) from exc
    if row.kind == "fill" and row.ref_id:
        fill = session.get(Fill, row.ref_id)
        if fill is not None:
            fill.reversed_by = reversal.event_id
            session.flush()
            if fill.intent_id:
                refresh_intent_fill(session, fill.intent_id)
    return reversal


# -- holdings entry ---------------------------------------------------------------------------

SYMBOL = re.compile(r"(\d{6})")


def parse_holdings_text(text: str) -> tuple[list[dict], list[str]]:
    """Rows "code, quantity[, cost price]" separated by commas, tabs or spaces
    (pasted from Excel or a broker export); a header line is ignored.
    Codes may carry exchange prefixes or suffixes (sh600000, 600000.SH)."""
    rows, errors = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        # Tabs (Excel) or spaces separate the columns when present; the commas then belong to the
        # numbers (1,000).  Otherwise the line is comma separated.
        line = line.strip()
        separator = r"\t+" if "\t" in line else r"[\s;]+" if len(line.split()) > 1 else r"[,;，]+"
        parts = [p.strip(" ,，").replace(",", "") for p in re.split(separator, line)]
        parts = [p for p in parts if p]
        if not parts:
            continue
        match = SYMBOL.search(parts[0])
        if match is None:
            if number == 1:
                continue  # header
            errors.append(f"第 {number} 行：无法识别代码 {parts[0]!r}")
            continue
        try:
            qty = int(float(parts[1])) if len(parts) > 1 else None
            price = float(parts[2]) if len(parts) > 2 else None
        except ValueError:
            errors.append(f"第 {number} 行：数量或成本价不是数字")
            continue
        if qty is None or qty < 0:
            errors.append(f"第 {number} 行：数量缺失或为负")
            continue
        rows.append({"symbol": match.group(1), "qty": qty, "cost_price": price})
    return rows, errors


def _ledger_mark(session: Session, account_id: str) -> str:
    """Changes whenever an event is added to the account (seq only grows)."""
    count, top = session.execute(select(func.count(), func.max(PositionEvent.seq)).where(
        PositionEvent.account_id == account_id)).one()
    return f"{count}:{top or 0}"


def preview_holdings(session: Session, account: Account, *, as_of: date, cash_fen: int, rows: list[dict],
                     known_symbols: set[str], op: Operator, filename: str | None = None,
                     now: datetime | None = None) -> ImportBatch:
    if account.mode != "manual":
        raise ReviewError("paper_account", "模拟账户的持仓由系统维护")
    last = session.scalar(select(func.max(PositionEvent.trade_date)).where(
        PositionEvent.account_id == account.account_id))
    errors = []
    if last is not None and as_of < last:
        errors.append(f"持仓日期 {as_of} 早于账户最后一笔记录 {last}")
    if as_of > _today(now or utc_now()):
        errors.append(f"持仓日期 {as_of} 晚于今天")
    if cash_fen < 0:
        errors.append("现金不能为负")
    merged: dict[str, dict] = {}
    for row in rows:
        if row["symbol"] not in known_symbols:
            errors.append(f"{row['symbol']} 不在证券主数据中")
        if row["symbol"] in merged:
            errors.append(f"{row['symbol']} 重复出现")
        merged[row["symbol"]] = row
    ledger = replay(session, account.account_id)
    diff = []
    for symbol in sorted(set(merged) | set(ledger.positions)):
        current = ledger.quantity(symbol)
        new = int(merged[symbol]["qty"]) if symbol in merged else 0
        if new != current:
            diff.append({"symbol": symbol, "current": current, "new": new, "change": new - current})
    summary = {"as_of": as_of.isoformat(), "cash_before_fen": ledger.cash_fen, "cash_after_fen": cash_fen,
               "rows": [{k: r[k] for k in ("symbol", "qty", "cost_price")} for r in merged.values()],
               "diff": diff, "errors": errors, "ledger_mark": _ledger_mark(session, account.account_id)}
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    count = session.scalar(select(func.count()).select_from(ImportBatch)) or 0
    batch = ImportBatch(batch_id=f"imp-{account.account_id}-{as_of:%Y%m%d}-{count + 1:04d}",
                        account_id=account.account_id, kind="holdings", filename=filename, sha256=digest,
                        rows=len(merged), status="previewed", summary=summary, created_by=op.actor)
    session.add(batch)
    session.flush()
    return batch


def commit_holdings(session: Session, account: Account, batch_id: str, op: Operator, reason: str) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None or batch.account_id != account.account_id or batch.kind != "holdings":
        raise ReviewError("not_found", "导入批次不存在")
    if batch.status != "previewed":
        raise ReviewError("invalid_state", f"批次状态为 {batch.status}")
    summary = batch.summary or {}
    if summary.get("errors"):
        raise ReviewError("invalid_batch", "预览中有错误，修正后重新预览")
    as_of = date.fromisoformat(summary["as_of"])
    if summary.get("ledger_mark") != _ledger_mark(session, account.account_id):
        raise ReviewError("stale_preview", "账户在预览后发生了变化，请重新预览")
    ledger = replay(session, account.account_id)
    rows = {r["symbol"]: r for r in summary["rows"]}
    new_events = []
    for item in summary["diff"]:
        symbol = item["symbol"]
        if ledger.quantity(symbol) != item["current"]:
            raise ReviewError("stale_preview", "账户在预览后发生了变化，请重新预览")
        row = rows.get(symbol)
        cost = int(round(row["qty"] * row["cost_price"] * 100)) if row and row.get("cost_price") else (
            ledger.positions[symbol].cost_fen * item["new"] // item["current"] if item["current"] else 0)
        new_events.append(PositionEvent(
            event_id=f"{batch_id}-{symbol}", account_id=account.account_id, trade_date=as_of, kind="adjustment",
            symbol=symbol, payload={"old_quantity": item["current"], "new_quantity": item["new"], "cost_fen": cost},
            ref_id=batch_id, reason=reason, created_by=op.actor))
    cash_delta = int(summary["cash_after_fen"]) - ledger.cash_fen
    if cash_delta:
        new_events.append(PositionEvent(event_id=f"{batch_id}-cash", account_id=account.account_id,
                                        trade_date=as_of, kind="cash", payload={"amount_fen": cash_delta},
                                        ref_id=batch_id, reason=reason, created_by=op.actor))
    _validate(session, account.account_id, new_events)
    session.add_all(new_events)
    before = account.holdings_confirmed_date
    account.holdings_confirmed_date = as_of
    batch.status = "committed"
    session.flush()
    audit(session, op.actor, "holdings.commit", "account", account.account_id,
          before={"confirmed": before.isoformat() if before else None},
          after={"confirmed": as_of.isoformat(), "batch": batch_id, "changes": len(summary["diff"]),
                 "cash_delta_fen": cash_delta}, reason=reason, **op.audit_kwargs())
    return batch
