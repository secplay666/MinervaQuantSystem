from __future__ import annotations

from dataclasses import dataclass, field, replace

from .events import (
    CashDeposited,
    LedgerEvent,
    PositionDelisted,
    SessionClosed,
    SharesAdjusted,
    TradeFilled,
)


class LedgerInvariantError(RuntimeError):
    """An event would break an accounting invariant; nothing was applied."""


@dataclass(frozen=True)
class Position:
    quantity: int
    sellable: int
    cost_fen: int  # total cost basis including buy fees


@dataclass
class Ledger:
    """Cash and positions derived only from applied events.

    Invariants: cash >= 0, 0 <= sellable <= quantity, event ids unique
    (re-applying an event is a no-op), and replaying the event log from an
    empty ledger reproduces the state exactly.
    """

    cash_fen: int = 0
    positions: dict[str, Position] = field(default_factory=dict)
    events: list[LedgerEvent] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def apply(self, event: LedgerEvent) -> bool:
        if event.event_id in self._seen:
            return False
        handler = {
            CashDeposited: self._deposit,
            TradeFilled: self._trade,
            SharesAdjusted: self._adjust,
            PositionDelisted: self._delist,
            SessionClosed: self._close,
        }[type(event)]
        handler(event)
        self._seen.add(event.event_id)
        self.events.append(event)
        return True

    def quantity(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return position.quantity if position else 0

    def sellable(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return position.sellable if position else 0

    # -- handlers -------------------------------------------------------------

    def _deposit(self, event: CashDeposited) -> None:
        if event.amount_fen <= 0:
            raise LedgerInvariantError(f"{event.event_id}: deposit must be positive")
        self.cash_fen += event.amount_fen

    def _trade(self, event: TradeFilled) -> None:
        if event.quantity <= 0 or event.price_fen <= 0:
            raise LedgerInvariantError(f"{event.event_id}: non-positive quantity or price")
        if event.notional_fen != event.quantity * event.price_fen:
            raise LedgerInvariantError(f"{event.event_id}: notional != quantity * price")
        position = self.positions.get(event.symbol, Position(0, 0, 0))
        cash_after = self.cash_fen + event.cash_delta_fen
        if event.side == "buy":
            if cash_after < 0:
                raise LedgerInvariantError(f"{event.event_id}: insufficient cash ({self.cash_fen} fen)")
            updated = Position(position.quantity + event.quantity, position.sellable,
                               position.cost_fen + event.notional_fen + event.fees_fen)
        elif event.side == "sell":
            if event.quantity > position.sellable:
                raise LedgerInvariantError(
                    f"{event.event_id}: selling {event.quantity} > sellable {position.sellable} (T+1)"
                )
            remaining = position.quantity - event.quantity
            cost_left = position.cost_fen * remaining // position.quantity if position.quantity else 0
            updated = Position(remaining, position.sellable - event.quantity, cost_left)
        else:
            raise LedgerInvariantError(f"{event.event_id}: unknown side {event.side}")
        self.cash_fen = cash_after
        self._store(event.symbol, updated)

    def _adjust(self, event: SharesAdjusted) -> None:
        position = self.positions.get(event.symbol)
        if position is None or position.quantity != event.old_quantity:
            raise LedgerInvariantError(f"{event.event_id}: adjustment does not match the holding")
        if event.new_quantity < 0 or event.cash_in_lieu_fen < 0:
            raise LedgerInvariantError(f"{event.event_id}: negative adjustment")
        if position.sellable == position.quantity:
            sellable = event.new_quantity
        else:
            sellable = min(event.new_quantity, int(position.sellable * event.step))
        self.cash_fen += event.cash_in_lieu_fen
        self._store(event.symbol, replace(position, quantity=event.new_quantity, sellable=sellable))

    def _delist(self, event: PositionDelisted) -> None:
        position = self.positions.get(event.symbol)
        if position is None or position.quantity != event.quantity:
            raise LedgerInvariantError(f"{event.event_id}: delisting does not match the holding")
        if event.proceeds_fen < 0:
            raise LedgerInvariantError(f"{event.event_id}: negative proceeds")
        self.cash_fen += event.proceeds_fen
        self._store(event.symbol, Position(0, 0, 0))

    def _close(self, event: SessionClosed) -> None:
        for symbol, position in list(self.positions.items()):
            if position.sellable != position.quantity:
                self.positions[symbol] = replace(position, sellable=position.quantity)

    def _store(self, symbol: str, position: Position) -> None:
        if position.quantity == 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = position

    # -- verification -----------------------------------------------------------

    def replayed(self) -> "Ledger":
        """A fresh ledger built from this ledger's event log."""
        fresh = Ledger()
        for event in self.events:
            fresh.apply(event)
        return fresh

    def check(self) -> None:
        if self.cash_fen < 0:
            raise LedgerInvariantError(f"negative cash {self.cash_fen}")
        for symbol, position in self.positions.items():
            if not 0 <= position.sellable <= position.quantity or position.quantity <= 0:
                raise LedgerInvariantError(f"{symbol}: invalid position {position}")
