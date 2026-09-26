from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bt_fakes import DatesSchedule, FixedStrategy, SyntheticMarket, config, rules, weekdays
from quant_system.backtest.engine import BacktestEngine
from quant_system.backtest.market_data import MarketData

DAYS = weekdays(date(2024, 1, 2), 30)


def run(market: SyntheticMarket, targets: dict[date, dict[str, float]], data: MarketData | None = None, **overrides):
    cfg = config(run={"start": str(DAYS[0]), "end": str(market.sessions[-1]), "initial_capital_cny": "1000000"},
                 **overrides)
    strategy = FixedStrategy(targets)
    engine = BacktestEngine(cfg, data or market.build(), rules(), strategy, DatesSchedule(set(targets)))
    return engine.run()


def flat(symbols: dict[str, float], days: list[date] = DAYS) -> dict[str, list[float | None]]:
    return {symbol: [price] * len(days) for symbol, price in symbols.items()}


def test_signal_at_close_fills_next_open_with_fees() -> None:
    market = SyntheticMarket(DAYS, flat({"600001": 10.0}), opens={"600001": {1: 10.2}})
    result = run(market, {DAYS[0]: {"600001": 1.0}})
    fill = result.fills.iloc[0]
    assert fill["session"] == DAYS[1] and fill["price_fen"] == 1020
    # Sized pre-open at the reference price 10.00: floor(1,000,000 / 10.00) = 100,000 shares,
    # then reduced to what the cash affords at 10.20 including fees.
    assert fill["quantity"] % 100 == 0 and fill["quantity"] * 1020 + fill["commission_fen"] + fill["transfer_fee_fen"] <= 100_000_000
    nav = result.nav.set_index("session")
    assert nav.loc[DAYS[0], "nav_fen"] == 100_000_000
    assert nav.loc[DAYS[1], "cash_fen"] == 100_000_000 - fill["notional_fen"] - fill["commission_fen"] - fill["transfer_fee_fen"]


def test_ex_rights_keeps_value_continuous_with_cash_in_lieu() -> None:
    closes = {"600001": [10.0] * 5 + [7.69] * 25}
    market = SyntheticMarket(DAYS, closes, factors={"600001": [(DAYS[5], 1.3)]}, opens={"600001": {5: 7.69}})
    result = run(market, {DAYS[0]: {"600001": 0.5}})
    action = result.corporate_actions.iloc[0]
    assert action["session"] == DAYS[5]
    assert action["new_quantity"] == int(action["old_quantity"] * 1.3)
    nav = result.nav.set_index("session")["nav_fen"]
    # 10.00 / 1.3 = 7.6923 -> reference 7.69; value drifts only by the rounded reference.
    assert abs(nav[DAYS[5]] / nav[DAYS[4]] - 1) < 1e-3


def test_delisted_holding_is_settled_at_the_last_close() -> None:
    closes = {"600001": [10.0] * 10 + [None] * 20, "600002": [10.0] * 30}
    market = SyntheticMarket(DAYS, closes, delist_dates={"600001": DAYS[15]})
    result = run(market, {DAYS[0]: {"600001": 0.5, "600002": 0.4}})
    settled = result.delistings.iloc[0]
    assert settled["session"] == DAYS[15] and settled["settlement_price_fen"] == 1000
    assert "600001" not in set(result.positions[result.positions["session"] == DAYS[16]]["symbol"])


def test_suspended_target_is_retried_and_participation_cap_splits_fills() -> None:
    closes = {"600001": [10.0, None, None] + [10.0] * 27, "000001": [10.0] * 30}
    market = SyntheticMarket(DAYS, closes, volumes={"000001": 200_000.0, "600001": 10_000_000.0})
    result = run(market, {DAYS[0]: {"600001": 0.5, "000001": 0.4}}, execution__max_participation=0.1)
    rejected = result.rejections
    assert set(rejected[rejected["symbol"] == "600001"]["rule_id"]) == {"SUSPENDED"}
    first_fill = result.fills[result.fills["symbol"] == "600001"]["session"].min()
    assert first_fill == DAYS[3]  # retried until it trades again
    capped = result.fills[result.fills["symbol"] == "000001"]
    # 40,000 shares wanted, at most 10% of 200,000 per session: filled over
    # consecutive sessions by retrying the residual.
    assert (capped["quantity"] <= 20_000).all() and len(capped) >= 2
    assert list(capped["session"]) == DAYS[1:1 + len(capped)]
    assert capped["quantity"].sum() >= 39_000


def test_minimum_lot_above_target_is_skipped() -> None:
    market = SyntheticMarket(DAYS, flat({"688001": 3000.0, "600001": 10.0}))
    result = run(market, {DAYS[0]: {"688001": 0.1, "600001": 0.5}})
    assert list(result.skipped["symbol"]) == ["688001"]  # 200 shares x 3000 > 100k target
    assert "688001" not in set(result.fills["symbol"])


def test_rebalance_sells_first_and_uses_proceeds_the_same_day() -> None:
    market = SyntheticMarket(DAYS, flat({"600001": 10.0, "600002": 20.0}))
    result = run(market, {DAYS[0]: {"600001": 0.95}, DAYS[5]: {"600002": 0.95}})
    day = result.fills[result.fills["session"] == DAYS[6]]
    assert list(day["side"]) == ["sell", "buy"]
    assert day.iloc[1]["notional_fen"] > 90_000_000


def test_no_trade_days_reconcile_with_holding_returns() -> None:
    rng = np.random.default_rng(7)
    closes = {symbol: list(np.round(10 * np.cumprod(1 + rng.normal(0, 0.02, len(DAYS))), 2))
              for symbol in ("600001", "000002", "300003")}
    market = SyntheticMarket(DAYS, closes)
    result = run(market, {DAYS[0]: {"600001": 0.3, "000002": 0.3, "300003": 0.3}})
    nav = result.nav.set_index("session")
    positions = result.positions
    traded = set(result.fills["session"])
    for before, after in zip(DAYS[:-1], DAYS[1:]):
        if after in traded or before not in set(positions["session"]):
            continue
        held = positions[positions["session"] == before]
        implied = nav.loc[before, "cash_fen"] + sum(
            row.quantity * int(round(closes[row.symbol][DAYS.index(after)] * 100)) for row in held.itertuples()
        )
        assert implied == nav.loc[after, "nav_fen"]


# ---------------------------------------------------------------- properties


def _random_market(seed: int, days: list[date]) -> SyntheticMarket:
    rng = np.random.default_rng(seed)
    symbols = ["600001", "600002", "000003", "300004", "688005"]
    closes: dict[str, list[float | None]] = {}
    for symbol in symbols:
        path = np.round(20 * np.cumprod(1 + rng.normal(0, 0.03, len(days))), 2)
        closes[symbol] = [None if rng.random() < 0.05 else float(max(price, 0.5)) for price in path]
        closes[symbol][0] = float(path[0])
    factors = {"000003": [(days[len(days) // 2], 1.25)]}
    return SyntheticMarket(days, closes, factors=factors, delist_dates={"600002": days[-5]})


def _targets(days: list[date]) -> dict[date, dict[str, float]]:
    return {
        days[0]: {"600001": 0.3, "600002": 0.3, "000003": 0.3},
        days[10]: {"000003": 0.4, "300004": 0.3, "688005": 0.25},
        days[25]: {"600001": 0.45, "300004": 0.45},
    }


def _comparable(result, until: date) -> dict[str, pd.DataFrame]:
    frames = {}
    for name in ("nav", "fills", "positions", "rejections"):
        frame = getattr(result, name)
        if not frame.empty:
            frame = frame[frame["session"] <= until].reset_index(drop=True)
        frames[name] = frame if not frame.empty else pd.DataFrame()
    return frames


def _assert_same(left: dict[str, pd.DataFrame], right: dict[str, pd.DataFrame]) -> None:
    for name in left:
        pd.testing.assert_frame_equal(left[name], right[name], check_dtype=False)


@settings(max_examples=12, derandomize=True, deadline=None)
@given(seed=st.integers(0, 10_000), cut=st.integers(12, 34))
def test_truncating_future_data_leaves_the_past_unchanged(seed: int, cut: int) -> None:
    days = weekdays(date(2024, 1, 2), 40)
    market = _random_market(seed, days)
    full = run(market, _targets(days))
    frames = market.frames()
    until = days[cut]
    for name in ("bars", "factors"):
        column = "trade_date" if name == "bars" else "effective_date"
        frames[name] = frames[name][pd.to_datetime(frames[name][column]).dt.date <= until]
    truncated = run(market, {d: w for d, w in _targets(days).items() if d <= until},
                    data=MarketData.from_frames(frames))
    # Orders placed at the close of `until` execute later; compare through `until`.
    _assert_same(_comparable(full, until), _comparable(truncated, until))


@settings(max_examples=12, derandomize=True, deadline=None)
@given(seed=st.integers(0, 10_000), cut=st.integers(12, 34), scale=st.floats(0.5, 2.0))
def test_perturbing_the_future_leaves_the_past_unchanged(seed: int, cut: int, scale: float) -> None:
    days = weekdays(date(2024, 1, 2), 40)
    market = _random_market(seed, days)
    base = run(market, _targets(days))
    frames = market.frames()
    until = days[cut]
    bars = frames["bars"].copy()
    later = pd.to_datetime(bars["trade_date"]).dt.date > until
    for column in ("open", "high", "low", "close"):
        bars.loc[later, column] = (bars.loc[later, column] * scale).round(2).clip(lower=0.01)
    ipo = bars[bars["symbol"] == "600001"].copy()
    ipo = ipo[pd.to_datetime(ipo["trade_date"]).dt.date > until].assign(symbol="600009")
    frames["bars"] = pd.concat([bars, ipo], ignore_index=True)
    master = frames["master"].copy()
    master.loc[master["symbol"] == "600002", "delist_date"] = until + timedelta(days=3)
    frames["master"] = pd.concat([master, pd.DataFrame([{"symbol": "600009", "board": "SSE_MAIN", "exchange": "SSE",
                                                         "list_date": until + timedelta(days=1),
                                                         "delist_date": None}])], ignore_index=True)
    frames["factors"] = pd.concat([frames["factors"], pd.DataFrame(
        [{"symbol": "600001", "effective_date": until + timedelta(days=1), "hfq_factor": 3.0}])], ignore_index=True)
    perturbed = run(market, _targets(days), data=MarketData.from_frames(frames))
    _assert_same(_comparable(base, until), _comparable(perturbed, until))


def test_runs_are_deterministic_and_input_order_does_not_matter() -> None:
    days = weekdays(date(2024, 1, 2), 40)
    market = _random_market(11, days)
    first, second = run(market, _targets(days)), run(market, _targets(days))
    frames = market.frames()
    frames = {name: frame.sample(frac=1.0, random_state=3).reset_index(drop=True) for name, frame in frames.items()}
    shuffled = run(market, _targets(days), data=MarketData.from_frames(frames))
    last = days[-1]
    _assert_same(_comparable(first, last), _comparable(second, last))
    _assert_same(_comparable(first, last), _comparable(shuffled, last))
    assert first.checks["replay_matches"]


def test_view_refuses_future_rows() -> None:
    from quant_system.backtest.view import LookAheadError, PanelView

    data = _random_market(1, weekdays(date(2024, 1, 2), 10)).build()
    view = PanelView(data, 3)
    with pytest.raises(LookAheadError):
        view.close_fen(lag=-1)
    assert view.adjusted_price(lag=3).shape == (len(data.symbols),)
