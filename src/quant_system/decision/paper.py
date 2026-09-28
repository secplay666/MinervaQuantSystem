"""Paper execution (ADR-008 §2, §6): approved intents of paper accounts are
filled at the next open with the backtest's fill model, one session at a
time, after that session's ingest.

Per session d, in order (mirrors backtest.engine):
  1. corporate actions on holdings (hfq step: integer shares, cash in lieu);
  2. delisting settlements;
  3. intents superseded by a newer decision become ``unfilled``;
  4. executable intents -- approved or modified before 09:15 on d (so nobody
     approves after seeing the open), execute_on <= d, not closed -- sells
     first, then buys by seq: bar required, no lock at the open, T+1
     sellable, participation cap, affordable, lot rules; price is the open
     with slippage against us, clamped to the band;
  5. unfilled buys retry for ``buy_retry_sessions`` sessions, sells until a
     newer decision takes over;
  6. snapshot marked at d's close; ``accounts.paper_through = d``.

Everything written is deterministic (ids from intent and session), and a
session is processed only once, so reruns are no-ops.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from fractions import Fraction

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app.db.models import Account, Approval, DecisionRun, Fill, OrderIntent
from ..backtest.config import BacktestConfig
from ..backtest.fills import fill_price_fen, mark_price_fen, open_state, reference_price_fen
from ..backtest.market_data import MarketData
from ..data_platform.sessions import SHANGHAI_TZ
from ..domain.fees import FeeSchedule
from ..domain.rules import MarketRules
from ..ledger import Ledger, PositionDelisted, SessionClosed, SharesAdjusted, TradeFilled
from .accounts import add_event, mark, replay, write_snapshots

APPROVAL_CUTOFF = dtime(9, 15)  # call auction opens: later approvals trade from the next session
OPEN_STATES = ("approved", "modified")


@dataclass
class PaperOutcome:
    account_id: str
    sessions: list[date] = field(default_factory=list)
    fills: int = 0
    unfilled: int = 0
    notes: list[str] = field(default_factory=list)


def approved_at(session: Session, intent_id: str) -> datetime | None:
    return session.scalar(select(Approval.at).where(
        Approval.intent_id == intent_id, Approval.action.in_(("approve", "modify", "override")))
        .order_by(Approval.at.desc()).limit(1))


def cutoff(day: date) -> datetime:
    return datetime.combine(day, APPROVAL_CUTOFF, SHANGHAI_TZ)


class PaperBroker:
    def __init__(self, session: Session, account: Account, market: MarketData, rules: MarketRules,
                 config: BacktestConfig) -> None:
        self.session, self.account, self.market, self.rules, self.config = session, account, market, rules, config
        self.fees = FeeSchedule(rules, config.commission_rate, config.commission_min_fen)
        self.ledger: Ledger = replay(session, account.account_id)
        self.outcome = PaperOutcome(account.account_id)

    # -- ledger writes ------------------------------------------------------------------------

    def _record(self, day: date, kind: str, event, payload: dict, symbol: str | None, ref_id: str | None = None,
                reason: str | None = None) -> None:
        self.ledger.apply(event)
        add_event(self.session, self.account.account_id, day, kind, payload, event_id=event.event_id,
                  actor="paper", symbol=symbol, ref_id=ref_id, reason=reason)

    def corporate_actions(self, i: int) -> None:
        day, market = self.market.sessions[i], self.market
        for symbol in sorted(self.ledger.positions):
            j = market.symbol_index(symbol)
            before, now = market.hfq[i - 1, j], market.hfq[i, j]
            if not (np.isfinite(before) and np.isfinite(now)) or abs(now / before - 1) < 1e-9:
                continue
            step = float(now / before)
            quantity = self.ledger.quantity(symbol)
            exact = quantity * step
            new_quantity = int(math.floor(exact + 1e-9))
            reference = reference_price_fen(market, i, j) or 0
            cash = int(math.floor((exact - new_quantity) * reference + 0.5))
            event_id = f"{self.account.account_id}-ca-{day:%Y%m%d}-{symbol}"
            self._record(day, "corporate_action", SharesAdjusted(event_id, day, symbol, quantity, new_quantity, cash, step),
                         {"old_quantity": quantity, "new_quantity": new_quantity, "cash_in_lieu_fen": cash, "step": step},
                         symbol, reason="除权除息")

    def delistings(self, i: int) -> None:
        day, market = self.market.sessions[i], self.market
        for symbol in sorted(self.ledger.positions):
            j = market.symbol_index(symbol)
            delisted = market.delist_date[j]
            if delisted is None or delisted > day:
                continue
            quantity = self.ledger.quantity(symbol)
            last = int(market.last_bar[i - 1, j]) if i > 0 else -1
            last_close = int(market.close[last, j]) if last >= 0 else 0
            price = last_close if self.config.delisting_settlement == "last_close" else 0
            event_id = f"{self.account.account_id}-delist-{day:%Y%m%d}-{symbol}"
            self._record(day, "delisting", PositionDelisted(event_id, day, symbol, quantity, price, quantity * price),
                         {"quantity": quantity, "settlement_price_fen": price, "proceeds_fen": quantity * price},
                         symbol, reason="退市结算")

    # -- intents --------------------------------------------------------------------------------

    def open_intents(self, day: date) -> list[OrderIntent]:
        query = select(OrderIntent).where(
            OrderIntent.account_id == self.account.account_id, OrderIntent.status.in_(OPEN_STATES),
            OrderIntent.execution.is_(None) | (OrderIntent.execution == "partial"), OrderIntent.execute_on <= day)
        return list(self.session.scalars(query.order_by(OrderIntent.trade_date, OrderIntent.seq)))

    def close_superseded(self, day: date, intents: list[OrderIntent]) -> list[OrderIntent]:
        """Intents of an older decision close when a newer decision's intents become executable."""
        latest = self.session.scalar(select(DecisionRun.trade_date).where(
            DecisionRun.account_id == self.account.account_id, DecisionRun.status == "complete",
            DecisionRun.kind.in_(("rebalance", "forced")), DecisionRun.next_session <= day)
            .order_by(DecisionRun.trade_date.desc()).limit(1))
        keep = []
        for intent in intents:
            if latest is not None and intent.trade_date < latest:
                self._close(intent, f"{day} 被更新的决策取代")
            else:
                keep.append(intent)
        return keep

    def _close(self, intent: OrderIntent, note: str) -> None:
        """No more attempts: ``unfilled`` if nothing traded, else ``partial_closed``."""
        intent.execution = "partial_closed" if intent.filled_qty else "unfilled"
        intent.execution_note = note
        self.outcome.unfilled += 1

    def execute(self, i: int) -> None:
        day = self.market.sessions[i]
        intents = self.close_superseded(day, self.open_intents(day))
        ready = []
        for intent in intents:
            approved = approved_at(self.session, intent.intent_id)
            if approved is None or approved > cutoff(day):
                continue  # approved after the call auction opened: trades from the next session
            ready.append((intent, approved))
        for intent, approved in sorted(ready, key=lambda x: (x[0].side != "sell", x[0].trade_date, x[0].seq)):
            self._fill(i, intent, approved)

    def _fill(self, i: int, intent: OrderIntent, approved: datetime) -> None:
        day, market, config = self.market.sessions[i], self.market, self.config
        j = market.symbol_index(intent.symbol)
        remaining = intent.qty - intent.filled_qty
        # Sessions already tried: from the first one it was approved in time for.
        attempt = sum(1 for s in market.sessions if intent.execute_on <= s < day and approved <= cutoff(s))
        state = open_state(market, self.rules, i, j)
        reason = None
        quantity = remaining
        lot = self.rules.lot_rule(str(market.board[j]))
        if not state.has_bar:
            reason, quantity = "停牌", 0
        elif (state.lock_buy if intent.side == "buy" else state.lock_sell):
            reason, quantity = "开盘涨停" if intent.side == "buy" else "开盘跌停", 0
        if quantity and intent.side == "sell":
            holding = self.ledger.quantity(intent.symbol)
            legal = lot.round_sell(min(quantity, self.ledger.sellable(intent.symbol)), holding)
            if legal < quantity:
                reason = "可卖数量不足"
            quantity = legal
        if quantity:
            cap = int(market.volume[i, j] * config.max_participation)
            if quantity > cap:
                quantity = lot.round_buy(cap) if intent.side == "buy" else lot.round_sell(cap, self.ledger.quantity(intent.symbol))
                reason = f"超过成交量的 {config.max_participation:.0%}"
        price = fill_price_fen(intent.side, state, config.slippage) if state.has_bar else 0
        exchange = str(market.exchange[j])
        if quantity and intent.side == "buy":
            while quantity > 0:
                notional = quantity * price
                if notional + self.fees.fees("buy", exchange, notional, day).total_fen <= self.ledger.cash_fen:
                    break
                per_share = Fraction(price) * (1 + self.fees.commission_rate)
                guess = int((self.ledger.cash_fen - self.fees.commission_min_fen) / per_share) if self.ledger.cash_fen > 0 else 0
                quantity = lot.round_buy(min(quantity - 1, max(guess, 0)))
                reason = "资金不足"
        if quantity > 0:
            notional = quantity * price
            fees = self.fees.fees(intent.side, exchange, notional, day)
            fill_id = f"paper-{intent.intent_id}-{day:%Y%m%d}"
            event = TradeFilled(f"{fill_id}-event", day, intent.symbol, intent.side, quantity, price, notional,
                                fees.commission_fen, fees.stamp_duty_fen, fees.transfer_fee_fen)
            self._record(day, "fill", event, {"side": intent.side, "quantity": quantity, "price_fen": price,
                                              "notional_fen": notional, "commission_fen": fees.commission_fen,
                                              "stamp_duty_fen": fees.stamp_duty_fen,
                                              "transfer_fee_fen": fees.transfer_fee_fen},
                         intent.symbol, ref_id=fill_id)
            self.session.add(Fill(fill_id=fill_id, account_id=self.account.account_id, intent_id=intent.intent_id,
                                  trade_date=day, symbol=intent.symbol, side=intent.side, qty=quantity, price_fen=price,
                                  commission_fen=fees.commission_fen, stamp_duty_fen=fees.stamp_duty_fen,
                                  transfer_fee_fen=fees.transfer_fee_fen, source="paper", created_by="paper"))
            intent.filled_qty += quantity
            self.outcome.fills += 1
        if intent.filled_qty >= intent.qty:
            intent.execution, intent.execution_note = "filled", f"{day} 成交"
        elif intent.side == "buy" and attempt + 1 >= config.buy_retry_sessions:
            self._close(intent, f"{day} {reason or '未成交'}，重试 {config.buy_retry_sessions} 个交易日后放弃")
        else:
            intent.execution = "partial" if intent.filled_qty else None
            intent.execution_note = f"{day} {reason or '部分成交'}，下一交易日继续"

    def close_session(self, i: int) -> None:
        day = self.market.sessions[i]
        self.ledger.apply(SessionClosed(f"{self.account.account_id}-close-{day:%Y%m%d}", day))
        write_snapshots(self.session, self.account.account_id, mark(self.ledger, self.market, i))


def run_paper(session: Session, account: Account, market: MarketData, rules: MarketRules, config: BacktestConfig,
              through: date) -> PaperOutcome:
    """Simulate every unprocessed session of ``account`` up to ``through`` (inclusive)."""
    if account.mode != "paper":
        raise ValueError(f"{account.account_id} is not a paper account")
    start = account.paper_through or (account.start_date - timedelta(days=1))
    broker = PaperBroker(session, account, market, rules, config)
    for day in market.sessions:
        if day <= start or day > through:
            continue
        i = market.session_index(day)
        broker.corporate_actions(i)
        broker.delistings(i)
        broker.execute(i)
        broker.close_session(i)
        account.paper_through = day
        broker.outcome.sessions.append(day)
    session.flush()
    return broker.outcome
