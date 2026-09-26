from __future__ import annotations

from datetime import date

import numpy as np

from bt_fakes import SyntheticMarket, rules, weekdays
from quant_system.backtest.view import PanelView
from quant_system.portfolio.sizing import SizingPolicy, size_orders
from quant_system.strategy.momentum import MomentumParams, MomentumStrategy

DAYS = weekdays(date(2023, 1, 2), 60)
PARAMS = MomentumParams(lookback=20, skip=5, top_n=2, min_window_coverage=0.9, min_listing_sessions=10,
                        min_avg_turnover_cny=0, turnover_window=5)


def path(growth: float) -> list[float | None]:
    return [round(10 * (1 + growth) ** index, 2) for index in range(len(DAYS))]


def test_momentum_ranks_by_skipped_window_and_breaks_ties_by_symbol() -> None:
    closes = {"600001": path(0.01), "600002": path(0.02), "600003": path(0.02), "600004": path(-0.01)}
    view = PanelView(SyntheticMarket(DAYS, closes).build(), 40)
    target = MomentumStrategy(PARAMS).on_close(view)
    assert list(target.weights) == ["600002", "600003"]  # equal scores: lower code first
    assert target.weights == {"600002": 0.5, "600003": 0.5}
    assert target.explanations["600002"]["rank"] == 1


def test_momentum_skips_the_most_recent_sessions() -> None:
    late_spike = path(0.0)
    late_spike[-5:] = [50.0] * 5  # only inside the skip window at t = 59
    closes = {"600001": late_spike, "600002": path(0.005), "600003": path(-0.02)}
    target = MomentumStrategy(PARAMS).on_close(PanelView(SyntheticMarket(DAYS, closes).build(), 59))
    assert "600001" not in list(target.weights)[:1]


def test_eligibility_filters_are_point_in_time() -> None:
    closes = {
        "600001": path(0.01),
        "600002": path(0.03),                          # recently listed: too young at t=40
        "000003": path(0.02),                          # SZSE ST during the window
        "600004": [None if 20 <= i < 30 else p for i, p in enumerate(path(0.04))],  # coverage too low
        "830005": path(0.05),                          # BSE excluded by board
    }
    market = SyntheticMarket(
        DAYS, closes, list_dates={"600002": DAYS[35]},
        risk=[{"symbol": "000003", "status": "ST", "start_date": DAYS[30], "end_date": None,
               "method": "szse_name_change"}],
    )
    view = PanelView(market.build(), 40)
    eligible = dict(zip(view.symbols, MomentumStrategy(PARAMS).eligible(view)))
    assert eligible == {"000003": False, "600001": True, "600002": False, "600004": False, "830005": False}
    # The ST interval starts after t=25, so at t=25 the name is still eligible.
    earlier = dict(zip(view.symbols, MomentumStrategy(PARAMS).eligible(PanelView(market.build(), 25))))
    assert earlier["000003"]


def test_sizing_band_lots_and_order_sequence() -> None:
    r = rules()
    lots = {"600001": r.lot_rule("SSE_MAIN"), "688001": r.lot_rule("STAR"), "000001": r.lot_rule("SZSE_MAIN")}
    result = size_orders(
        weights={"600001": 0.5, "688001": 0.3},
        ranks={"600001": 2, "688001": 1},
        holdings={"600001": 48_000, "000001": 1_234},
        prices_fen={"600001": 1000, "688001": 123_45, "000001": 500},
        nav_fen=100_000_000,
        lot_rules=lots,
        policy=SizingPolicy(cash_buffer=0.0, band_rel=0.1),
    )
    assert result.within_band == ["600001"]  # 480k held vs 500k target: inside the band
    assert [(o.symbol, o.side, o.quantity) for o in result.orders] == [
        ("000001", "sell", 1_234),   # exit sells everything, odd lot included
        ("688001", "buy", 2_430),    # 300k / 123.45 = 2430.1 -> STAR 1-share steps
    ]


def test_sizing_skips_names_whose_minimum_lot_exceeds_the_target() -> None:
    r = rules()
    result = size_orders({"688001": 0.01}, {"688001": 1}, {}, {"688001": 300_00}, 10_000_000,
                         {"688001": r.lot_rule("STAR")}, SizingPolicy(cash_buffer=0.0))
    assert result.orders == [] and result.skipped == [("688001", 0.01, "min_lot_exceeds_target")]
    assert np.isclose(sum(weight for _, weight, _ in result.skipped), 0.01)
