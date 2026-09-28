"""Accounts and their event ledger in the business database (ADR-008 §2, §9).

The state of an account is the replay of its ``position_events`` ordered by
(trade_date, seq) through :class:`quant_system.ledger.Ledger`, so the same
invariants hold as in backtests (cash never negative, T+1 sellable, unique
event ids).  A session boundary is inserted between trade dates, and after
the last one, because a decision after T's close is executed on T+1 when
everything held is sellable.  Reversals void an earlier event; they are
only accepted when the remaining events still replay cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..app.audit import audit
from ..app.db.models import Account, AccountSnapshot, PositionEvent, PositionSnapshot
from ..backtest.fills import mark_price_fen
from ..backtest.market_data import MarketData
from ..domain.entities import TargetPortfolio
from ..ledger import (
    CashAdjusted,
    CashDeposited,
    Ledger,
    LedgerEvent,
    LedgerInvariantError,
    PositionAdjusted,
    PositionDelisted,
    SessionClosed,
    SharesAdjusted,
    TradeFilled,
)
from ..strategy.base import PortfolioState

MODES = ("manual", "paper")
EVENT_KINDS = ("deposit", "fill", "corporate_action", "delisting", "adjustment", "cash", "reversal")


class UnknownSymbolError(ValueError):
    """A holding refers to a security the market data does not know."""


def create_account(session: Session, root: Path, *, account_id: str, name: str, mode: str,
                   strategy_config: str, initial_cash_fen: int, start_date: date, actor: str,
                   note: str | None = None) -> Account:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not (root / strategy_config).is_file():
        raise FileNotFoundError(f"strategy config {strategy_config} not found under {root}")
    if initial_cash_fen < 0:
        raise ValueError("initial cash must not be negative")
    account = Account(account_id=account_id, name=name, mode=mode, strategy_config=strategy_config,
                      initial_cash_fen=initial_cash_fen, start_date=start_date, created_by=actor, note=note,
                      holdings_confirmed_date=start_date if mode == "manual" and initial_cash_fen else None)
    session.add(account)
    session.flush()
    if initial_cash_fen:
        add_event(session, account_id, start_date, "deposit", {"amount_fen": initial_cash_fen},
                  event_id=f"{account_id}-deposit-initial", actor=actor, reason="initial capital")
    audit(session, actor, "account.create", "account", account_id,
          after={"name": name, "mode": mode, "strategy_config": strategy_config,
                 "initial_cash_fen": initial_cash_fen, "start_date": start_date.isoformat()})
    return account


def add_event(session: Session, account_id: str, trade_date: date, kind: str, payload: dict[str, Any], *,
              event_id: str, actor: str, symbol: str | None = None, ref_id: str | None = None,
              reason: str | None = None) -> PositionEvent:
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {kind!r}")
    row = PositionEvent(event_id=event_id, account_id=account_id, trade_date=trade_date, kind=kind, symbol=symbol,
                        payload=payload, ref_id=ref_id, reason=reason, created_by=actor)
    session.add(row)
    session.flush()
    return row


def to_ledger_event(row: PositionEvent) -> LedgerEvent:
    p, day = row.payload, row.trade_date
    if row.kind == "deposit":
        return CashDeposited(row.event_id, day, int(p["amount_fen"]))
    if row.kind == "fill":
        return TradeFilled(row.event_id, day, str(row.symbol), p["side"], int(p["quantity"]), int(p["price_fen"]),
                           int(p["notional_fen"]), int(p["commission_fen"]), int(p["stamp_duty_fen"]),
                           int(p["transfer_fee_fen"]))
    if row.kind == "corporate_action":
        return SharesAdjusted(row.event_id, day, str(row.symbol), int(p["old_quantity"]), int(p["new_quantity"]),
                              int(p["cash_in_lieu_fen"]), float(p["step"]))
    if row.kind == "delisting":
        return PositionDelisted(row.event_id, day, str(row.symbol), int(p["quantity"]),
                                int(p["settlement_price_fen"]), int(p["proceeds_fen"]))
    if row.kind == "adjustment":
        return PositionAdjusted(row.event_id, day, str(row.symbol), int(p["old_quantity"]), int(p["new_quantity"]),
                                int(p["cost_fen"]))
    if row.kind == "cash":
        return CashAdjusted(row.event_id, day, int(p["amount_fen"]))
    raise ValueError(f"{row.event_id}: {row.kind} is not a ledger event")


def replay_rows(account_id: str, rows: list[PositionEvent]) -> Ledger:
    voided = {row.payload["reverses"] for row in rows if row.kind == "reversal"}
    ledger = Ledger()
    current: date | None = None
    for row in rows:
        if row.kind == "reversal" or row.event_id in voided:
            continue
        if current is not None and row.trade_date > current:
            ledger.apply(SessionClosed(f"{account_id}-close-{current:%Y%m%d}", current))
        current = row.trade_date
        ledger.apply(to_ledger_event(row))
    if current is not None:
        ledger.apply(SessionClosed(f"{account_id}-close-{current:%Y%m%d}", current))
    ledger.check()
    return ledger


def account_events(session: Session, account_id: str, through: date | None = None) -> list[PositionEvent]:
    query = select(PositionEvent).where(PositionEvent.account_id == account_id)
    if through is not None:
        query = query.where(PositionEvent.trade_date <= through)
    return list(session.scalars(query.order_by(PositionEvent.trade_date, PositionEvent.seq)))


def replay(session: Session, account_id: str, through: date | None = None) -> Ledger:
    """Account state after the close of ``through`` (all events if None)."""
    return replay_rows(account_id, account_events(session, account_id, through))


def reverse_event(session: Session, account_id: str, event_id: str, *, trade_date: date, actor: str,
                  reason: str) -> PositionEvent:
    rows = account_events(session, account_id)
    target = next((row for row in rows if row.event_id == event_id), None)
    if target is None or target.kind == "reversal":
        raise ValueError(f"{event_id} is not a reversible event of {account_id}")
    if any(row.kind == "reversal" and row.payload.get("reverses") == event_id for row in rows):
        raise ValueError(f"{event_id} is already reversed")
    reversal = PositionEvent(event_id=f"{event_id}-reversed", account_id=account_id, trade_date=trade_date,
                             kind="reversal", payload={"reverses": event_id}, ref_id=event_id, reason=reason,
                             created_by=actor)
    try:
        replay_rows(account_id, [*rows, reversal])
    except LedgerInvariantError as exc:
        raise ValueError(f"reversing {event_id} breaks the ledger: {exc}") from exc
    session.add(reversal)
    session.flush()
    audit(session, actor, "position_event.reverse", "position_event", event_id, reason=reason)
    return reversal


@dataclass(frozen=True)
class MarkedAccount:
    """Holdings after T's close, marked at T's close (ADR-002 §4 marks)."""

    session: date
    cash_fen: int
    market_value_fen: int
    nav_fen: int
    quantities: dict[str, int]
    marks: dict[str, int]
    costs: dict[str, int]

    @property
    def weights(self) -> dict[str, float]:
        if self.nav_fen <= 0:
            return {symbol: 0.0 for symbol in self.quantities}
        return {s: self.quantities[s] * self.marks[s] / self.nav_fen for s in sorted(self.quantities)}

    def portfolio_state(self, previous_target: TargetPortfolio | None) -> PortfolioState:
        return PortfolioState(session=self.session, nav_fen=self.nav_fen, cash_fen=self.cash_fen,
                              quantities=dict(self.quantities), weights=self.weights,
                              previous_target=previous_target)


def mark(ledger: Ledger, market: MarketData, i: int) -> MarkedAccount:
    quantities, marks, costs = {}, {}, {}
    value = 0
    for symbol, position in sorted(ledger.positions.items()):
        try:
            j = market.symbol_index(symbol)
        except KeyError:
            raise UnknownSymbolError(f"holding {symbol} is not in the market data") from None
        price = mark_price_fen(market, i, j) or 0
        quantities[symbol], marks[symbol], costs[symbol] = position.quantity, price, position.cost_fen
        value += position.quantity * price
    day = market.sessions[i]
    return MarkedAccount(day, ledger.cash_fen, value, ledger.cash_fen + value, quantities, marks, costs)


def write_snapshots(session: Session, account_id: str, marked: MarkedAccount) -> None:
    """Replace the day's snapshot rows (a cache of the replay, rebuilt on rerun)."""
    session.execute(delete(PositionSnapshot).where(PositionSnapshot.account_id == account_id,
                                                   PositionSnapshot.trade_date == marked.session))
    session.execute(delete(AccountSnapshot).where(AccountSnapshot.account_id == account_id,
                                                  AccountSnapshot.trade_date == marked.session))
    session.add(AccountSnapshot(account_id=account_id, trade_date=marked.session, cash_fen=marked.cash_fen,
                                market_value_fen=marked.market_value_fen, nav_fen=marked.nav_fen,
                                positions=len(marked.quantities)))
    for symbol in sorted(marked.quantities):
        qty, price = marked.quantities[symbol], marked.marks[symbol]
        session.add(PositionSnapshot(account_id=account_id, trade_date=marked.session, symbol=symbol, qty=qty,
                                     mark_fen=price, value_fen=qty * price, cost_fen=marked.costs[symbol]))
