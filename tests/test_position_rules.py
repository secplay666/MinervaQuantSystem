"""Structure rules: lifecycle, entry gate, ladder, exits, replay (position manager, phase 1)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from quant_system.position.rules import (
    ACTIVE, BASE, EXHAUSTED, LEFT, PENDING, REALIZED, RIGHT, TOP, IndexDay, RuleParams, Structure, base_prompts,
    replay,
)
from quant_system.position.stages import StageParams

D0 = date(2026, 1, 5)
S = Structure(neckline=10.0, target=20.0, effective=D0)
P = RuleParams()


def days(n: int) -> list[date]:
    return [D0 + timedelta(days=k) for k in range(n)]


def run(closes, labels=((D0, RIGHT),), index_label=RIGHT, index_completion=0.5, structure=S, index=None):
    dates = days(len(closes))
    if index is None:
        index = {d: IndexDay(index_label, index_completion) for d in dates}
    return replay(dates, np.array(closes, dtype=float), structure, list(labels), index, P)


def rules(outcome) -> list[tuple[int, str, float]]:
    return [((e.trade_date - D0).days, e.rule, e.weight) for e in outcome.events]


def test_a_full_round_enters_reduces_on_the_ladder_and_exits_on_the_trailing_stop() -> None:
    out = run([9.5, 10.5, 17.0, 18.0, 19.0, 20.5, 16.0])
    assert rules(out) == [
        (1, "activated", 0.0), (1, "entry", 1.0),
        (2, "ladder", 0.7), (3, "ladder", 0.5),
        (4, "realized", 0.5), (4, "ladder", 0.25), (4, "top_watch", 0.25),
        (5, "target", 0.2),  # the main index at 50%: keep 20%
        (6, "exhausted", 0.2), (6, "trailing", 0.0),  # 16.0 is 22% below the 20.5 high
    ]
    assert out.phase == EXHAUSTED and out.weight == 0
    proceeds = 0.3 * 17 + 0.2 * 18 + 0.25 * 19 + 0.05 * 20.5 + 0.2 * 16
    assert out.round_return == pytest.approx(proceeds / 10.5 - 1)
    assert out.script_return == pytest.approx(1.0)


def test_the_index_gate_waits_then_enters_half_size_late_in_the_index_move() -> None:
    dates = days(4)
    index = {dates[0]: IndexDay(BASE), dates[1]: IndexDay(BASE), dates[2]: IndexDay(RIGHT, 0.95),
             dates[3]: IndexDay(RIGHT, 0.95)}
    out = run([9.8, 10.4, 10.6, 11.0], index=index)
    assert rules(out) == [(1, "activated", 0.0), (1, "waiting_index", 0.0), (2, "entry", 0.5)]
    assert "指数晚期" in out.events[-1].message and out.entry_weight == 0.5
    assert out.waiting_for.startswith("持有 100%")  # of the (half) entry


def test_a_false_break_exits_and_a_new_close_above_the_neckline_enters_again() -> None:
    out = run([10.5, 11.0, 9.4, 9.8, 10.6, 12.0])
    assert rules(out) == [(0, "activated", 0.0), (0, "entry", 1.0), (2, "false_break", 0.0), (4, "entry", 1.0)]
    assert len(out.segments) == 2 and out.segments[0]["return"] == pytest.approx(9.4 / 10.5 - 1)
    assert out.segments[1]["exit_date"] is None  # still open, valued at the last close
    assert out.round_return == pytest.approx((9.4 / 10.5) * (12.0 / 10.6) - 1)


def test_no_new_entry_after_the_trailing_stop_ends_the_round() -> None:
    out = run([10.5, 17.5, 13.5, 13.0, 14.0])  # 70% reached, then 23% off the high
    assert [r for _, r, _ in rules(out)] == ["activated", "entry", "ladder", "trailing"]
    assert out.waiting_for == "本轮已离场，等待下一轮结构"


def test_the_trailing_stop_waits_until_seventy_percent() -> None:
    out = run([10.5, 16.0, 12.5, 12.4])  # 60% at most, then 22% off the high: an early shake-out
    assert [r for _, r, _ in rules(out)] == ["activated", "entry"] and out.weight == 1.0


def test_labels_silence_the_rules_and_top_allows_exits_only() -> None:
    out = run([10.5, 11.0, 12.0], labels=[(D0, LEFT)])
    assert [r for _, r, _ in rules(out)] == ["activated"] and out.waiting_for == "左侧：规则休眠"
    dates = days(4)
    out = run([10.5, 17.2, 9.0, 12.0], labels=[(D0, RIGHT), (dates[1], TOP)])
    assert [r for _, r, _ in rules(out)] == ["activated", "entry", "ladder", "false_break"]  # no re-entry under 顶部


def test_a_top_neckline_break_exits_when_observed() -> None:
    structure = Structure(10.0, 20.0, D0, top_neckline=18.0, top_mode="observe")
    out = run([10.5, 18.5, 19.2, 17.8], structure=structure)
    assert rules(out)[-1] == (3, "top_break", 0.0)


def test_the_scan_starts_on_the_effective_date_not_with_the_history() -> None:
    dates = days(6)
    structure = Structure(10.0, 20.0, dates[3])
    out = run([10.5, 12.0, 11.0, 9.9, 10.2, 10.4], structure=structure)
    assert out.activated_on == dates[4] and rules(out)[0] == (4, "activated", 0.0)
    assert Structure(10.0, 20.0, dates[3]).price_at(0.7) == pytest.approx(17.0)
    assert out.phase == ACTIVE and out.next_step == "完成度 70%" and out.next_price == pytest.approx(17.0)


def test_pending_structures_say_how_far_the_neckline_is() -> None:
    out = run([9.0, 9.2])
    assert out.phase == PENDING and out.waiting_for.startswith("等突破颈线（距 +8.7%")


def test_base_prompts_fire_once_per_criterion_while_the_label_is_base() -> None:
    n = 60
    dates = days(n)
    closes = np.full(n, 9.0)
    opens = np.full(n, 8.9)
    volumes = np.full(n, 1e6)
    volumes[35:50] = 5e5
    volumes[50] = 1.6e6
    closes[55:] = 10.5  # above the new neckline
    events = base_prompts(dates, closes, opens, volumes, [(D0, BASE)], 10.0, StageParams())
    assert [(e.rule, (e.trade_date - D0).days) for e in events] == [("base_volume", 50), ("base_neckline", 55)]
    assert all(e.priority == 4 for e in events)
    assert base_prompts(dates, closes, opens, volumes, [(D0, RIGHT)], 10.0, StageParams()) == []


def test_rule_parameters_validate() -> None:
    p = RuleParams.from_dict({"trailing": 0.15, "ladder": [[0.6, 0.8], [0.8, 0.4]]})
    assert p.trailing == 0.15 and p.ladder == ((0.6, 0.8), (0.8, 0.4))
    with pytest.raises(ValueError):
        RuleParams.from_dict({"ladder": [[0.8, 0.5], [0.7, 0.7]]})  # not increasing
    assert RuleParams.from_dict(RuleParams().to_dict()) == RuleParams()
    assert REALIZED == "realized"
