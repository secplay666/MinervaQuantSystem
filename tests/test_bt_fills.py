from __future__ import annotations

from datetime import date
from fractions import Fraction

from bt_fakes import SyntheticMarket, rules, weekdays
from quant_system.backtest.fills import fill_price_fen, mark_price_fen, open_state, reference_price_fen

DAYS = weekdays(date(2024, 3, 4), 6)


def market(**kwargs) -> SyntheticMarket:
    base = {"sessions": DAYS, "closes": {"600001": [20.0, 10.1, 10.2, 10.3, 10.4, 10.5]}}
    base.update(kwargs)
    return SyntheticMarket(**base)


def test_ex_rights_open_is_not_mistaken_for_limit_down() -> None:
    # 10-for-10 bonus on DAYS[1]: raw halves, hfq doubles; open 10.10 vs ref 10.00.
    data = market(factors={"600001": [(DAYS[1], 2.0)]}, opens={"600001": {1: 10.1}}).build()
    assert reference_price_fen(data, 1, 0) == 1000
    state = open_state(data, rules(), 1, 0)
    assert state.band_in_force and state.lock_sell is None and (state.up_fen, state.down_fen) == (1100, 900)


def test_limit_up_at_open_blocks_buys_and_one_price_down_blocks_sells() -> None:
    data = market(closes={"600001": [10.0, 11.0, 9.9, 10.0, 10.0, 10.0]},
                  opens={"600001": {1: 11.0, 2: 9.9}}, highs={"600001": {1: 11.0, 2: 9.9}},
                  lows={"600001": {1: 10.8, 2: 9.9}}).build()
    up = open_state(data, rules(), 1, 0)
    assert up.lock_buy == "LIMIT_UP_AT_OPEN" and up.lock_sell is None
    down = open_state(data, rules(), 2, 0)  # O=H=L=C=9.90 is exactly the limit-down price
    assert down.lock_sell in {"LIMIT_DOWN_AT_OPEN", "ONE_PRICE_LIMIT_DOWN"} and down.lock_buy is None


def test_band_not_in_force_is_flagged_not_blocked() -> None:
    # A relisting-style gap far outside the 10% band: the band is evidently not in force.
    data = market(closes={"600001": [10.0, 3.0, 3.1, 3.2, 3.3, 3.4]}, opens={"600001": {1: 3.2}}).build()
    state = open_state(data, rules(), 1, 0)
    assert not state.band_in_force and state.lock_sell is None
    assert "LIMIT_BAND_NOT_IN_FORCE" in state.flags


def test_inferred_st_lock_applies_to_sse_main_only_when_history_is_unknown() -> None:
    bar = {"opens": {"600001": {1: 10.5}, "000001": {1: 10.5}},
           "highs": {"600001": {1: 10.5}, "000001": {1: 10.5}},
           "lows": {"600001": {1: 10.3}, "000001": {1: 10.3}}}
    data = market(closes={"600001": [10.0, 10.4, 10.4, 10.4, 10.4, 10.4],
                          "000001": [10.0, 10.4, 10.4, 10.4, 10.4, 10.4]}, **bar).build()
    sse = open_state(data, rules(), 1, data.symbol_index("600001"))
    szse = open_state(data, rules(), 1, data.symbol_index("000001"))
    assert sse.lock_buy == "LIMIT_LOCK_INFERRED_ST"
    assert szse.lock_buy is None  # SZSE risk history is dated: normal stock, 10% band


def test_bulletin_history_makes_sse_status_known_but_not_bse() -> None:
    # One bulletin-derived SSE interval marks the whole SSE history as dated:
    # an SSE stock without intervals is then known-normal (no inferred lock).
    risk = [{"symbol": "600002", "status": "ST", "start_date": DAYS[3], "end_date": None,
             "method": "bars:first_bar_after_suspension", "source": "sse_bulletin"}]
    closes = [10.0, 10.4, 10.4, 10.4, 10.4, 10.4]
    data = market(closes={"600001": closes, "600002": closes, "920001": closes},
                  opens={"600001": {1: 10.5}}, highs={"600001": {1: 10.5}}, lows={"600001": {1: 10.3}},
                  risk=risk).build()
    known = dict(zip(data.symbols, data.risk_known))
    assert known == {"600001": True, "600002": True, "920001": False}
    assert open_state(data, rules(), 1, data.symbol_index("600001")).lock_buy is None


def test_known_st_uses_the_five_percent_band() -> None:
    risk = [{"symbol": "000001", "status": "ST", "start_date": DAYS[0], "end_date": None,
             "method": "szse_name_change"}]
    data = market(closes={"000001": [10.0, 10.5, 10.5, 10.5, 10.5, 10.5]},
                  opens={"000001": {1: 10.5}}, risk=risk).build()
    state = open_state(data, rules(), 1, 0)
    assert (state.up_fen, state.down_fen) == (1050, 950) and state.lock_buy == "LIMIT_UP_AT_OPEN"


def test_slippage_is_clamped_to_the_band_and_listing_day_has_no_band() -> None:
    data = market(closes={"600001": [10.0, 10.99, 11.0, 11.0, 11.0, 11.0]}, opens={"600001": {1: 10.99}}).build()
    state = open_state(data, rules(), 1, 0)
    assert fill_price_fen("buy", state, Fraction(1, 100)) == 1100
    assert fill_price_fen("sell", state, Fraction(1, 100)) == 1088  # 10.99 * 0.99 = 10.8801
    first = open_state(data, rules(), 0, 0)
    assert "NO_REFERENCE_PRICE" in first.flags and not first.band_in_force


def test_suspended_session_has_no_bar_and_marks_carry_through_ex_rights() -> None:
    data = market(closes={"600001": [10.0, None, None, 5.1, 5.2, 5.3]},
                  factors={"600001": [(DAYS[2], 2.0)]}).build()
    assert not open_state(data, rules(), 1, 0).has_bar
    assert mark_price_fen(data, 1, 0) == 1000
    assert mark_price_fen(data, 2, 0) == 500  # ex-date during the suspension
    assert reference_price_fen(data, 3, 0) == 500
