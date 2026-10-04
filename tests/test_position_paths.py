"""Around a structure (phase 2): base zone, sentinels, path suggestion, days away."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from quant_system.position.paths import (
    BaseZone, Sentinel, base_zone_entry, days_away, mean_range, sentinel_crossing, suggest_path,
)
from quant_system.position.rules import LEFT, RIGHT, IndexDay, RuleParams, Structure, Zone, replay

D0 = date(2026, 1, 5)


def days(n: int) -> list[date]:
    return [D0 + timedelta(days=k) for k in range(n)]


def test_the_base_zone_turns_left_into_base_once_and_only_from_left() -> None:
    closes = np.array([12.0, 11.5, 10.8, 10.4, 10.9])
    zone = BaseZone(upper=11.0, lower=10.0, effective=D0, version=3)
    event = base_zone_entry(days(5), closes, [(D0, LEFT)], zone)
    assert (event.trade_date - D0).days == 2 and event.key == "base_zone:v3" and "自动转为筑底" in event.message
    assert base_zone_entry(days(5), closes, [(D0, RIGHT)], zone) is None
    assert "已关闭" in base_zone_entry(days(5), closes, [(D0, LEFT)], zone, auto=False).message
    later = BaseZone(upper=11.0, lower=10.0, effective=D0 + timedelta(days=4))
    assert base_zone_entry(days(5), closes, [(D0, LEFT)], later).trade_date == D0 + timedelta(days=4)


def test_a_sentinel_fires_on_the_first_close_through_it_in_its_direction() -> None:
    closes = np.array([10.0, 10.4, 10.6, 9.8])
    up = Sentinel(id=7, price=10.5, direction="up", effective=D0, source="前高", version=2)
    event = sentinel_crossing(days(4), closes, up)
    assert (event.trade_date - D0).days == 2 and event.priority == 2 and event.key == "sentinel:7:v2"
    assert event.message == "收盘站上哨兵（前高），是否更新方向标签？"
    down = Sentinel(id=8, price=9.9, direction="down", effective=D0)
    assert (sentinel_crossing(days(4), closes, down).trade_date - D0).days == 3
    assert sentinel_crossing(days(3), closes[:3], down) is None


def test_distance_in_days_of_the_average_range() -> None:
    close = np.array([10.0, 10.0, 10.0])
    high, low = np.array([10.2, 10.3, 10.1]), np.array([9.8, 9.9, 9.9])
    assert mean_range(high, low, close) == pytest.approx((0.04 + 0.02) / 2)
    assert days_away(10.9, 10.0, 0.03) == 3 and days_away(9.7, 10.0, 0.03) == 1
    assert days_away(11.0, 10.0, None) is None


def run(closes, structure):
    dates = days(len(closes))
    return replay(dates, np.array(closes, dtype=float), structure, [(D0, RIGHT)],
                  {d: IndexDay(RIGHT, 0.5) for d in dates}, RuleParams())


def test_the_path_suggestion_follows_the_close_against_the_round() -> None:
    s = Structure(10.0, 20.0, D0)
    out = run([10.5, 17.5, 13.5], s)  # trailing stop: the round is over, peak 17.5
    assert out.round_over and out.peak_close == 17.5
    b = suggest_path(s, out, 13.5)
    assert b["path"] == "B" and b["levels"]["base_zone"] == pytest.approx((10.0, 11.0)) and b["levels"]["target"] == 17.5
    a = suggest_path(s, out, 15.0)  # 33% of the move given back
    assert a["path"] == "A" and a["levels"]["buyback_zone"] == pytest.approx((13.75, 17.5 - 0.382 * 7.5))
    assert suggest_path(s, out, 17.0)["path"] == "C"
    held = run([10.5, 12.0], s)
    assert not held.round_over and suggest_path(s, held, 12.0) is None


def test_no_suggestion_while_a_buyback_zone_is_waiting() -> None:
    s = Structure(10.0, 20.0, D0, buyback=Zone(14.5, 16.0, 0.5, D0))
    out = run([10.5, 17.5, 13.5], s)
    assert not out.round_over and suggest_path(s, out, 13.5) is None
