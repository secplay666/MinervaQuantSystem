"""Session-by-session backtest loop (ADR-002).

Per session t:
  1. pre-open  corporate actions on held names (hfq step, integer shares +
               cash in lieu), delisting settlements, then order sizing from
               the target set after an earlier close, at reference prices;
  2. open      sells then buys, each checked for bar/lock/T+1/lots/
               participation/cash, filled at open +/- slippage;
  3. close     marks, snapshot, accounting identities; on a rebalance day the
               strategy sees a point-in-time view and sets the next target;
  4. end       SessionClosed releases T+1 quantities.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd

from ..domain.calendar import TradingCalendar
from ..domain.entities import TargetPortfolio
from ..domain.fees import FeeSchedule
from ..domain.rules import MarketRules
from ..ledger import (
    CashDeposited,
    Ledger,
    LedgerInvariantError,
    PositionDelisted,
    SessionClosed,
    SharesAdjusted,
    TradeFilled,
)
from ..portfolio.sizing import SizedOrder, size_orders
from ..strategy.base import MonthEndSchedule, PortfolioState, RebalanceSchedule, Strategy
from .config import BacktestConfig
from .fills import fill_price_fen, mark_price_fen, open_state, reference_price_fen
from .market_data import MarketData
from .view import PanelView

LOGGER = logging.getLogger(__name__)


@dataclass
class ActiveTarget:
    target: TargetPortfolio
    first_session: int
    outstanding: dict[str, str] = field(default_factory=dict)  # symbol -> side


@dataclass
class BacktestResult:
    config: BacktestConfig
    nav: pd.DataFrame
    positions: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    rejections: pd.DataFrame
    corporate_actions: pd.DataFrame
    delistings: pd.DataFrame
    signals: pd.DataFrame
    skipped: pd.DataFrame
    counters: dict[str, Any]
    checks: dict[str, Any]
    ledger: Ledger
    extras: dict[str, pd.DataFrame] = field(default_factory=dict)  # strategy report tables


class BacktestEngine:
    def __init__(
        self,
        config: BacktestConfig,
        data: MarketData,
        rules: MarketRules,
        strategy: Strategy,
        schedule: RebalanceSchedule | None = None,
    ) -> None:
        self.config = config
        self.data = data
        self.rules = rules
        self.strategy = strategy
        self.schedule = schedule or MonthEndSchedule()
        self.calendar = TradingCalendar(data.sessions)
        self.fees = FeeSchedule(rules, config.commission_rate, config.commission_min_fen)
        self.lot_rules = {str(symbol): rules.lot_rule(str(board)) for symbol, board in zip(data.symbols, data.board)}
        self.column = {str(symbol): j for j, symbol in enumerate(data.symbols)}
        self.ledger = Ledger()
        self.counters: Counter[str] = Counter()
        self._orders: list[dict[str, Any]] = []
        self._fills: list[dict[str, Any]] = []
        self._rejections: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._delistings: list[dict[str, Any]] = []
        self._signals: list[dict[str, Any]] = []
        self._skipped: list[dict[str, Any]] = []
        self._nav: list[dict[str, Any]] = []
        self._positions: list[dict[str, Any]] = []
        self._fees_total = 0
        self._order_seq = 0
        self._last_target: TargetPortfolio | None = None

    # ------------------------------------------------------------------ run

    def run(self) -> BacktestResult:
        start = self.calendar.index_on_or_after(self.config.start)
        end = self.calendar.index_on_or_before(self.config.end)
        if start > end:
            raise ValueError("backtest window contains no sessions")
        self.ledger.apply(CashDeposited("deposit-initial", self.data.sessions[start], self.config.initial_capital_fen))
        pending: tuple[int, TargetPortfolio] | None = None
        active: ActiveTarget | None = None
        for i in range(start, end + 1):
            day = self.data.sessions[i]
            events_before = len(self.ledger.events)
            cash_before = self.ledger.cash_fen
            self._corporate_actions(i)
            self._settle_delistings(i)
            if pending is not None and pending[0] == i:
                active = ActiveTarget(pending[1], i)
                pending = None
            traded = 0
            if active is not None:
                traded = self._execute(i, active)
            self._close(i, day, events_before, cash_before, traded)
            if self.schedule.is_rebalance(self.calendar, i):
                view = PanelView(self.data, i)
                if getattr(self.strategy, "wants_portfolio_state", False):
                    target = self.strategy.on_close(view, self._portfolio_state(i, day))
                else:
                    target = self.strategy.on_close(view)
                self._last_target = target
                self._record_signals(target)
                execute_at = i + self.config.delay_sessions
                if execute_at <= end:
                    pending = (execute_at, target)
            self.ledger.apply(SessionClosed(f"close-{day:%Y%m%d}", day))
        checks = self._final_checks()
        return BacktestResult(
            config=self.config,
            nav=pd.DataFrame(self._nav),
            positions=pd.DataFrame(self._positions),
            orders=pd.DataFrame(self._orders),
            fills=pd.DataFrame(self._fills),
            rejections=pd.DataFrame(self._rejections),
            corporate_actions=pd.DataFrame(self._actions),
            delistings=pd.DataFrame(self._delistings),
            signals=pd.DataFrame(self._signals),
            skipped=pd.DataFrame(self._skipped),
            counters=dict(sorted(self.counters.items())),
            checks=checks,
            ledger=self.ledger,
        )

    # ------------------------------------------------------------ pre-open

    def _corporate_actions(self, i: int) -> None:
        if i == 0:
            return
        day = self.data.sessions[i]
        for symbol in sorted(self.ledger.positions):
            j = self.column[symbol]
            before, now = self.data.hfq[i - 1, j], self.data.hfq[i, j]
            if not (np.isfinite(before) and np.isfinite(now)) or abs(now / before - 1) < 1e-9:
                continue
            step = float(now / before)
            quantity = self.ledger.quantity(symbol)
            exact = quantity * step
            new_quantity = int(math.floor(exact + 1e-9))
            reference = reference_price_fen(self.data, i, j) or 0
            cash_in_lieu = int(math.floor((exact - new_quantity) * reference + 0.5))
            self.ledger.apply(SharesAdjusted(f"ca-{day:%Y%m%d}-{symbol}", day, symbol, quantity, new_quantity,
                                             cash_in_lieu, step))
            self._actions.append({"session": day, "symbol": symbol, "step": step, "old_quantity": quantity,
                                  "new_quantity": new_quantity, "cash_in_lieu_fen": cash_in_lieu,
                                  "reference_fen": reference})
            self.counters["corporate_actions"] += 1

    def _settle_delistings(self, i: int) -> None:
        day = self.data.sessions[i]
        for symbol in sorted(self.ledger.positions):
            j = self.column[symbol]
            delisted = self.data.delist_date[j]
            if delisted is None or delisted > day:
                continue
            quantity = self.ledger.quantity(symbol)
            last = int(self.data.last_bar[i - 1, j]) if i > 0 else -1
            last_close = int(self.data.close[last, j]) if last >= 0 else 0
            price = last_close if self.config.delisting_settlement == "last_close" else 0
            self.ledger.apply(PositionDelisted(f"delist-{day:%Y%m%d}-{symbol}", day, symbol, quantity, price,
                                               quantity * price))
            self._delistings.append({"session": day, "symbol": symbol, "quantity": quantity,
                                     "settlement_price_fen": price, "proceeds_fen": quantity * price,
                                     "last_close_value_fen": quantity * last_close, "delist_date": delisted})
            self.counters["delisting_settlements"] += 1

    # ------------------------------------------------------------ execution

    def _pre_open_nav(self, i: int) -> tuple[int, dict[str, int | None]]:
        prices: dict[str, int | None] = {}
        value = 0
        for symbol, position in self.ledger.positions.items():
            price = reference_price_fen(self.data, i, self.column[symbol]) or 0
            prices[symbol] = price
            value += position.quantity * price
        return self.ledger.cash_fen + value, prices

    def _execute(self, i: int, active: ActiveTarget) -> int:
        day = self.data.sessions[i]
        target = active.target
        first = i == active.first_session
        if not first:
            for symbol, side in list(active.outstanding.items()):
                if side == "buy" and i - active.first_session >= self.config.buy_retry_sessions:
                    del active.outstanding[symbol]
                    self.counters["buy_retries_expired"] += 1
            if not active.outstanding:
                return 0
        nav, prices = self._pre_open_nav(i)
        weights = {symbol: weight for symbol, weight in target.weights.items() if self._tradable_target(symbol, day)}
        for symbol in weights:
            if symbol not in prices:
                prices[symbol] = reference_price_fen(self.data, i, self.column[symbol])
        ranks = {symbol: int(target.explanations.get(symbol, {}).get("rank", 0)) for symbol in weights}
        holdings = {symbol: position.quantity for symbol, position in self.ledger.positions.items()}
        sized = size_orders(weights, ranks, holdings, prices, nav, self.lot_rules, self.config.sizing,
                            symbols=None if first else set(active.outstanding))
        if first:
            for symbol, weight, reason in sized.skipped:
                self._skipped.append({"session": day, "symbol": symbol, "weight": weight, "reason": reason})
                self.counters[f"skipped_{reason}"] += 1
            self.counters["rebalances_executed"] += 1
        outstanding: dict[str, str] = {}
        traded = 0
        for order in sized.orders:
            filled, notional = self._fill(i, order)
            traded += notional
            if filled < order.quantity:
                outstanding[order.symbol] = order.side
        if first:
            active.outstanding = outstanding
        else:
            active.outstanding = {s: side for s, side in active.outstanding.items() if s in outstanding}
        return traded

    def _tradable_target(self, symbol: str, day: date) -> bool:
        delisted = self.data.delist_date[self.column[symbol]]
        return delisted is None or delisted > day

    def _new_order_id(self, day: date, order: SizedOrder) -> str:
        self._order_seq += 1
        return f"{day:%Y%m%d}-{self._order_seq:06d}-{order.symbol}-{order.side}"

    def _reject(self, day: date, order_id: str, order: SizedOrder, rule: str, decision: str,
                actual: object, limit: object, reason: str) -> None:
        self._rejections.append({"session": day, "order_id": order_id, "symbol": order.symbol, "side": order.side,
                                 "rule_id": rule, "decision": decision, "actual": str(actual),
                                 "limit": str(limit), "reason": reason})
        self.counters[f"{decision}_{rule}"] += 1

    def _fill(self, i: int, order: SizedOrder) -> tuple[int, int]:
        day = self.data.sessions[i]
        j = self.column[order.symbol]
        order_id = self._new_order_id(day, order)
        self._orders.append({"session": day, "order_id": order_id, "symbol": order.symbol, "side": order.side,
                             "quantity": order.quantity, "rank": order.rank, "reason": order.reason})
        self.counters["orders"] += 1
        state = open_state(self.data, self.rules, i, j)
        for flag in state.flags:
            self.counters[f"flag_{flag}"] += 1
        if not state.has_bar:
            self._reject(day, order_id, order, "SUSPENDED", "rejected", "no bar", "bar required", "停牌或无行情")
            return 0, 0
        lock = state.lock_buy if order.side == "buy" else state.lock_sell
        if lock:
            limit = state.up_fen if order.side == "buy" else state.down_fen
            self._reject(day, order_id, order, lock, "rejected", state.open_fen, limit, "开盘价触及涨跌停或一字板")
            return 0, 0
        lot = self.lot_rules[order.symbol]
        quantity = order.quantity
        holding = self.ledger.quantity(order.symbol)
        if order.side == "sell":
            sellable = self.ledger.sellable(order.symbol)
            legal = lot.round_sell(min(quantity, sellable), holding)
            if legal < quantity:
                self._reject(day, order_id, order, "T1_SELLABLE", "reduced", quantity, sellable, "可卖数量不足")
            quantity = legal
        cap = int(self.data.volume[i, j] * self.config.max_participation)
        if quantity > cap:
            reduced = lot.round_buy(cap) if order.side == "buy" else lot.round_sell(cap, holding)
            self._reject(day, order_id, order, "PARTICIPATION_CAP", "reduced", quantity, cap,
                         f"超过当日成交量的 {self.config.max_participation:.0%}")
            quantity = reduced
        price = fill_price_fen(order.side, state, self.config.slippage)
        exchange = str(self.data.exchange[j])
        if order.side == "buy" and quantity > 0:
            quantity = self._affordable(quantity, price, exchange, day, lot, order, order_id)
            if quantity <= 0:
                return 0, 0  # recorded as INSUFFICIENT_CASH
        if quantity <= 0:
            self._reject(day, order_id, order, "NOTHING_TO_FILL", "rejected", 0, order.quantity, "调整后数量为 0")
            return 0, 0
        notional = quantity * price
        fees = self.fees.fees(order.side, exchange, notional, day)
        event = TradeFilled(f"{order_id}-F", day, order.symbol, order.side, quantity, price, notional,
                            fees.commission_fen, fees.stamp_duty_fen, fees.transfer_fee_fen)
        self.ledger.apply(event)
        self._fees_total += fees.total_fen
        self._fills.append({"session": day, "fill_id": event.event_id, "order_id": order_id,
                            "symbol": order.symbol, "side": order.side, "quantity": quantity,
                            "price_fen": price, "open_fen": state.open_fen, "notional_fen": notional,
                            "commission_fen": fees.commission_fen, "stamp_duty_fen": fees.stamp_duty_fen,
                            "transfer_fee_fen": fees.transfer_fee_fen, "flags": ",".join(state.flags)})
        self.counters["fills"] += 1
        return quantity, notional

    def _affordable(self, quantity: int, price: int, exchange: str, day: date, lot, order, order_id) -> int:
        cash = self.ledger.cash_fen
        original = quantity
        while quantity > 0:
            notional = quantity * price
            if notional + self.fees.fees("buy", exchange, notional, day).total_fen <= cash:
                break
            per_share = Fraction(price) * (1 + self.fees.commission_rate)
            guess = int((cash - self.fees.commission_min_fen) / per_share) if cash > 0 else 0
            quantity = lot.round_buy(min(quantity - 1, max(guess, 0)))
        if quantity < original:
            self._reject(day, order_id, order, "INSUFFICIENT_CASH", "reduced" if quantity else "rejected",
                         original, quantity, "可用资金不足")
        return quantity

    # ---------------------------------------------------------------- close

    def _close(self, i: int, day: date, events_before: int, cash_before: int, traded: int) -> None:
        self.ledger.check()
        flow = 0
        for event in self.ledger.events[events_before:]:
            if isinstance(event, CashDeposited):
                flow += event.amount_fen
            elif isinstance(event, TradeFilled):
                flow += event.cash_delta_fen
            elif isinstance(event, SharesAdjusted):
                flow += event.cash_in_lieu_fen
            elif isinstance(event, PositionDelisted):
                flow += event.proceeds_fen
        if self.ledger.cash_fen != cash_before + flow:
            raise LedgerInvariantError(f"{day}: cash {self.ledger.cash_fen} != {cash_before} + flows {flow}")
        market_value = 0
        for symbol, position in sorted(self.ledger.positions.items()):
            j = self.column[symbol]
            mark = mark_price_fen(self.data, i, j) or 0
            value = position.quantity * mark
            market_value += value
            self._positions.append({"session": day, "symbol": symbol, "quantity": position.quantity,
                                    "mark_fen": mark, "value_fen": value, "cost_fen": position.cost_fen,
                                    "has_bar": bool(self.data.has_bar[i, j])})
        self._nav.append({"session": day, "cash_fen": self.ledger.cash_fen, "market_value_fen": market_value,
                          "nav_fen": self.ledger.cash_fen + market_value, "traded_fen": traded,
                          "fees_cum_fen": self._fees_total, "positions": len(self.ledger.positions)})

    def _portfolio_state(self, i: int, day: date) -> PortfolioState:
        """Holdings after the close of session ``i`` (the nav row just written)."""
        nav_fen = self._nav[-1]["nav_fen"]
        quantities: dict[str, int] = {}
        weights: dict[str, float] = {}
        for symbol, position in sorted(self.ledger.positions.items()):
            mark = mark_price_fen(self.data, i, self.column[symbol]) or 0
            quantities[symbol] = position.quantity
            weights[symbol] = position.quantity * mark / nav_fen if nav_fen > 0 else 0.0
        return PortfolioState(session=day, nav_fen=nav_fen, cash_fen=self.ledger.cash_fen,
                              quantities=quantities, weights=weights, previous_target=self._last_target)

    def _record_signals(self, target: TargetPortfolio) -> None:
        self.counters["rebalance_signals"] += 1
        for symbol, weight in sorted(target.weights.items()):
            row = {"as_of": target.as_of, "symbol": symbol, "weight": weight}
            row.update(target.explanations.get(symbol, {}))
            self._signals.append(row)

    def _final_checks(self) -> dict[str, Any]:
        replayed = self.ledger.replayed()
        if replayed.cash_fen != self.ledger.cash_fen or replayed.positions != self.ledger.positions:
            raise LedgerInvariantError("replaying the event log does not reproduce the ledger")
        return {
            "sessions": len(self._nav),
            "events": len(self.ledger.events),
            "cash_flow_identity": "passed every session",
            "ledger_invariants": "passed every session",
            "replay_matches": True,
        }
