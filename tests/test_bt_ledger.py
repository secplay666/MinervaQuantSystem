from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quant_system.ledger import (
    CashDeposited,
    Ledger,
    LedgerInvariantError,
    PositionDelisted,
    SessionClosed,
    SharesAdjusted,
    TradeFilled,
)

DAY = date(2024, 1, 2)


def buy(event_id: str, quantity: int, price: int, fees: int = 500, symbol: str = "600001") -> TradeFilled:
    return TradeFilled(event_id, DAY, symbol, "buy", quantity, price, quantity * price, fees, 0, 0)


def sell(event_id: str, quantity: int, price: int, fees: int = 500, stamp: int = 0) -> TradeFilled:
    return TradeFilled(event_id, DAY, "600001", "sell", quantity, price, quantity * price, fees, stamp, 0)


def funded(amount: int = 10_000_000) -> Ledger:
    ledger = Ledger()
    ledger.apply(CashDeposited("deposit", DAY, amount))
    return ledger


def test_buy_then_sell_flows_and_t_plus_one() -> None:
    ledger = funded()
    ledger.apply(buy("b1", 1000, 1000))
    assert ledger.cash_fen == 10_000_000 - 1_000_000 - 500
    with pytest.raises(LedgerInvariantError, match="T\\+1"):
        ledger.apply(sell("s-same-day", 100, 1000))
    ledger.apply(SessionClosed("close-1", DAY))
    ledger.apply(sell("s1", 400, 1100, stamp=220))
    assert ledger.quantity("600001") == 600
    assert ledger.cash_fen == 10_000_000 - 1_000_500 + 440_000 - 720
    assert ledger.positions["600001"].cost_fen == 1_000_500 * 600 // 1000


def test_duplicate_events_are_ignored_and_invalid_ones_rejected() -> None:
    ledger = funded()
    assert ledger.apply(buy("b1", 100, 1000))
    assert not ledger.apply(buy("b1", 100, 1000))
    assert ledger.quantity("600001") == 100
    with pytest.raises(LedgerInvariantError, match="insufficient cash"):
        ledger.apply(buy("b-too-big", 100_000, 1000))
    with pytest.raises(LedgerInvariantError, match="notional"):
        ledger.apply(TradeFilled("bad", DAY, "600001", "buy", 100, 1000, 99_999, 500, 0, 0))
    assert ledger.quantity("600001") == 100  # failed events change nothing


def test_corporate_action_and_delisting() -> None:
    ledger = funded()
    ledger.apply(buy("b1", 150, 1000))
    ledger.apply(SessionClosed("c1", DAY))
    ledger.apply(SharesAdjusted("ca1", DAY, "600001", 150, 195, 150, 1.301))
    assert ledger.quantity("600001") == 195 and ledger.sellable("600001") == 195
    cash = ledger.cash_fen
    ledger.apply(PositionDelisted("dl", DAY, "600001", 195, 80, 195 * 80))
    assert "600001" not in ledger.positions and ledger.cash_fen == cash + 195 * 80
    with pytest.raises(LedgerInvariantError):
        ledger.apply(SharesAdjusted("ca-missing", DAY, "600001", 195, 390, 0, 2.0))


def test_replay_reproduces_state() -> None:
    ledger = funded()
    ledger.apply(buy("b1", 200, 1234))
    ledger.apply(buy("b2", 300, 999, symbol="000001"))
    ledger.apply(SessionClosed("c1", DAY))
    ledger.apply(sell("s1", 100, 1300))
    replayed = ledger.replayed()
    assert replayed.cash_fen == ledger.cash_fen and replayed.positions == ledger.positions


@settings(max_examples=150, derandomize=True, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["buy", "sell", "close", "adjust"]),
                          st.integers(1, 30), st.integers(100, 5_000)), max_size=40))
def test_random_event_streams_keep_invariants(steps) -> None:
    ledger = funded(5_000_000)
    for number, (kind, lots, price) in enumerate(steps):
        event_id = f"e{number}"
        try:
            if kind == "buy":
                ledger.apply(buy(event_id, lots * 100, price))
            elif kind == "sell":
                ledger.apply(sell(event_id, lots * 100, price))
            elif kind == "close":
                ledger.apply(SessionClosed(event_id, DAY))
            elif ledger.quantity("600001"):
                quantity = ledger.quantity("600001")
                ledger.apply(SharesAdjusted(event_id, DAY, "600001", quantity, quantity * 2, 0, 2.0))
        except LedgerInvariantError:
            pass  # rejected events must leave the ledger untouched
        ledger.check()
        flows = sum(
            e.amount_fen if isinstance(e, CashDeposited) else e.cash_delta_fen if isinstance(e, TradeFilled)
            else e.cash_in_lieu_fen if isinstance(e, SharesAdjusted) else 0
            for e in ledger.events
        )
        assert ledger.cash_fen == flows
    replayed = ledger.replayed()
    assert replayed.cash_fen == ledger.cash_fen and replayed.positions == ledger.positions
