from __future__ import annotations

import copy
import json
from datetime import date
from fractions import Fraction

import pytest

from bt_fakes import RULES_PATH, rules
from quant_system.domain.calendar import TradingCalendar
from quant_system.domain.fees import FeeSchedule
from quant_system.domain.money import half_up, rate, yuan_to_fen
from quant_system.domain.rules import MarketRules


def test_price_limit_rule_switch_dates() -> None:
    r = rules()
    assert r.limit_ratio("CHINEXT", "normal", date(2020, 8, 21)) == Fraction(1, 10)
    assert r.limit_ratio("CHINEXT", "normal", date(2020, 8, 24)) == Fraction(1, 5)
    assert r.limit_ratio("CHINEXT", "risk_warning", date(2020, 8, 21)) == Fraction(1, 20)
    assert r.limit_ratio("SZSE_MAIN", "risk_warning", date(2026, 7, 3)) == Fraction(1, 20)
    assert r.limit_ratio("SZSE_MAIN", "risk_warning", date(2026, 7, 6)) == Fraction(1, 10)
    assert r.limit_ratio("STAR", "risk_warning", date(2022, 1, 4)) == Fraction(1, 5)
    assert r.limit_ratio("BSE", "normal", date(2022, 1, 4)) == Fraction(3, 10)


def test_limit_prices_round_half_up_with_one_tick_minimum() -> None:
    r = rules()
    assert r.limit_prices(395, rate("0.10")) == (435, 356)  # 4.345 -> 4.35, 3.555 -> 3.56
    assert r.limit_prices(1000, rate("0.05")) == (1050, 950)
    assert r.limit_prices(5, rate("0.10")) == (6, 4)  # below 0.10 the band is one tick
    assert r.limit_prices(1, rate("0.10")) == (2, 1)  # never below the minimum price


def test_no_limit_sessions_after_listing() -> None:
    r = rules()
    assert r.is_no_limit_session("SSE_MAIN", date(2023, 4, 10), 4)
    assert not r.is_no_limit_session("SSE_MAIN", date(2023, 4, 10), 5)
    assert r.is_no_limit_session("SSE_MAIN", date(2023, 3, 1), 0)  # pre-reform IPO day
    assert not r.is_no_limit_session("SSE_MAIN", date(2023, 3, 1), 1)
    assert r.is_no_limit_session("CHINEXT", date(2020, 8, 24), 4)
    assert not r.is_no_limit_session("CHINEXT", date(2020, 8, 21), 1)
    assert not r.is_no_limit_session("SSE_MAIN", None, None)


def test_lot_rules() -> None:
    r = rules()
    main, star, bse = r.lot_rule("SSE_MAIN"), r.lot_rule("STAR"), r.lot_rule("BSE")
    assert main.round_buy(350) == 300 and main.round_buy(99) == 0
    assert star.round_buy(199) == 0 and star.round_buy(201) == 201
    assert bse.round_buy(100) == 100 and bse.round_buy(137) == 137
    assert main.round_sell(120, 150) == 100  # the odd 50 stays
    assert main.round_sell(150, 150) == 150  # selling all includes the odd lot
    assert main.round_sell(30, 50) == 0  # an odd-lot balance is sold in one piece only
    assert star.round_sell(150, 300) == 0 and star.round_sell(250, 300) == 250


def test_fee_schedule_minimum_and_statutory_switches() -> None:
    fees = FeeSchedule(rules(), rate("0.00025"), yuan_to_fen(5))
    small = fees.fees("buy", "SSE", yuan_to_fen(10_000), date(2024, 1, 2))
    assert small.commission_fen == 500 and small.stamp_duty_fen == 0 and small.transfer_fee_fen == 10
    before = fees.fees("sell", "SZSE", yuan_to_fen(100_000), date(2023, 8, 25))
    after = fees.fees("sell", "SZSE", yuan_to_fen(100_000), date(2023, 8, 28))
    assert (before.stamp_duty_fen, after.stamp_duty_fen) == (10_000, 5_000)
    assert before.commission_fen == 2_500
    assert fees.fees("buy", "SSE", yuan_to_fen(100_000), date(2022, 4, 28)).transfer_fee_fen == 200
    assert fees.fees("buy", "SSE", yuan_to_fen(100_000), date(2022, 4, 29)).transfer_fee_fen == 100
    assert fees.fees("buy", "BSE", yuan_to_fen(100_000), date(2022, 4, 28)).transfer_fee_fen == 250


def test_rules_config_rejects_gaps_and_overlaps() -> None:
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    overlapping = copy.deepcopy(payload)
    overlapping["price_limits"].append({"boards": ["STAR"], "risk": ["normal"], "ratio": "0.10"})
    with pytest.raises(ValueError, match="price-limit rules match"):
        MarketRules(overlapping)
    gap = copy.deepcopy(payload)
    gap["stamp_duty"] = [item for item in gap["stamp_duty"] if item.get("from") != "2023-08-28"]
    with pytest.raises(ValueError, match="stamp duty"):
        MarketRules(gap)


def test_money_and_calendar_helpers() -> None:
    assert half_up(Fraction(5, 2)) == 3 and half_up(Fraction(7, 2)) == 4 and half_up(Fraction(-5, 2)) == -3
    assert yuan_to_fen("1000000") == 100_000_000
    calendar = TradingCalendar([date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1)])
    assert calendar.is_month_end(1) and not calendar.is_month_end(0) and calendar.is_month_end(2)
    assert calendar.index_on_or_before(date(2024, 2, 10)) == 2
