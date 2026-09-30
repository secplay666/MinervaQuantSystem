"""Four-stage views, base criteria and the stage backtest (position manager, phase 1)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from bt_fakes import SyntheticMarket, weekdays
from quant_system.position.stage_backtest import run_stage_backtest
from quant_system.position.stages import (
    ADVANCE, BASE, DECLINE, PRESETS, TOP, StageParams, classify, retest_holds, stage_view, volume_surge,
)

FAST = StageParams(ma=50, slope=10, flat=0.02, confirm=3, context=100)


def cycle() -> np.ndarray:
    """Up 150 sessions, flat 100, down 150, flat 150."""
    up = 100 * 1.004 ** np.arange(150)
    top = np.full(100, up[-1])
    down = top[-1] * 0.996 ** np.arange(1, 151)
    base = np.full(150, down[-1])
    return np.concatenate([up, top, down, base])


def test_a_full_cycle_passes_through_the_four_stages_in_order() -> None:
    stages = classify(cycle(), FAST)
    assert stages[:59].max() == 0  # the average and its slope do not exist yet
    seen = [int(s) for k, s in enumerate(stages) if s and (k == 0 or s != stages[k - 1])]
    assert seen == [ADVANCE, TOP, DECLINE, BASE]
    assert stages[140] == ADVANCE and stages[240] == TOP and stages[390] == DECLINE and stages[-1] == BASE


def test_a_short_dip_does_not_flip_the_stage() -> None:
    price = 100 * 1.004 ** np.arange(200)
    price[150:152] = price[150:152] * 0.9  # two sessions far below a still rising average
    stages = classify(price, FAST)
    assert set(stages[150:160]) == {ADVANCE}  # needs ``confirm`` sessions to change


def test_indices_use_their_own_flat_threshold() -> None:
    price = 1000 * 1.0006 ** np.arange(400)  # about 16% a year: 0.6% per 10 sessions
    p = StageParams(ma=50, slope=10, flat=0.02, index_flat=0.004, confirm=3, context=100)
    assert stage_view(price, p)["stage"] == "base"
    view = stage_view(price, p, is_index=True)
    assert view["stage"] == "advance" and "50 日均线" in view["reason"] and view["days"] > 100


def test_volume_criterion_needs_a_dry_spell_then_an_up_day_surge() -> None:
    n = 60
    volume = np.full(n, 1e6)
    volume[35:50] = 5e5  # dried up
    volume[50] = 1.6e6  # 3.2x the recent mean
    close = np.full(n, 10.0)
    open_ = np.full(n, 9.9)
    hits = volume_surge(close, open_, volume, StageParams())
    assert np.flatnonzero(hits).tolist() == [50]
    open_[50] = 10.1  # a down day is no surge
    assert not volume_surge(close, open_, volume, StageParams()).any()


def test_retest_criterion_needs_a_rebound_and_a_higher_low() -> None:
    close = np.array([10.0, 9.5, 9.0, 9.2, 9.6, 9.8, 9.5, 9.1])  # low 9.0, rebound to 9.8 (+8.9%), back at 9.1
    p = StageParams()
    assert retest_holds(close, 0, 7, p)
    assert not retest_holds(np.append(close[:-1], 8.9), 0, 7, p)  # broke the low
    assert not retest_holds(np.array([10, 9.5, 9.0, 9.1, 9.2, 9.1, 9.05]), 0, 6, p)  # no rebound first


def test_parameters_validate_and_presets_differ() -> None:
    assert PRESETS["steady"][1].ma == 200 and PRESETS["classic"][1].ma == 150 and PRESETS["short"][1].ma == 60
    p = StageParams.from_dict({"ma": "120", "flat": 0.01, "unknown": 1}, PRESETS["classic"][1])
    assert p.ma == 120 and p.flat == 0.01 and p.confirm == PRESETS["classic"][1].confirm
    with pytest.raises(ValueError):
        StageParams.from_dict({"ma": 5})


def test_the_backtest_reports_stages_transitions_and_books() -> None:
    sessions = weekdays(date(2020, 1, 6), 700)
    rng = np.random.default_rng(7)
    closes = {}
    for k, symbol in enumerate(("600001", "600002", "000001", "300001")):
        path = np.concatenate([cycle(), cycle()[:200]]) * (1 + 0.1 * k)
        closes[symbol] = list(np.round(path * (1 + rng.normal(0, 0.003, len(path))), 2))
    market = SyntheticMarket(sessions, closes).build()
    index = 4000 * 1.0005 ** np.arange(len(sessions))
    result = run_stage_backtest(market, index, FAST)
    assert {s["stage"] for s in result["stages"]} == {"base", "advance", "top", "decline"}
    assert sum(s["share"] for s in result["stages"]) == pytest.approx(1.0)
    assert any(t["count"] for t in result["transitions"])
    assert set(result["overall"]) == {"market", "stage2", "stage2_gated"}
    assert result["stocks"] == 4
    assert [y["year"] for y in result["yearly"]] == [2020, 2021, 2022]  # from the first year a stock is counted
