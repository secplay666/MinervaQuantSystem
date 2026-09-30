from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quant_system.analytics.etf_flows import fund_flows, group_flows
from quant_system.app.etf import EtfQueries
from test_api import env  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]
SESSIONS = [d.date() for d in pd.bdate_range("2025-01-01", periods=320)]


def _fund(symbol: str, shares: list[float], closes: list[float | None]) -> tuple[pd.DataFrame, pd.DataFrame]:
    days = SESSIONS[:len(shares)]
    return (pd.DataFrame({"trade_date": days, "symbol": symbol, "shares": shares}),
            pd.DataFrame({"trade_date": days, "symbol": symbol, "close": closes}).dropna())


def test_flows_leave_out_splits_gaps_and_first_records() -> None:
    shares = [1e8, 1.01e8, 1.02e8, 2.04e8, 2.06e8, 2.56e8, 2.58e8]  # 1:2 split on day 3, real inflow on day 5
    closes = [4.0, 4.0, 4.0, 2.0, 2.0, 2.0, 2.0]
    frame, prices = _fund("510300", shares, closes)
    flows = fund_flows(frame, prices, SESSIONS)
    assert flows["event"].tolist() == ["new", None, None, "split", None, None, None]
    assert flows["flow"].tolist()[1] == pytest.approx(1e6 * 4.0)
    assert pd.isna(flows["flow"].iloc[3])
    assert flows["flow"].iloc[5] == pytest.approx(5e7 * 2.0)


def test_a_split_whose_price_moves_a_day_later_is_still_recognised() -> None:
    shares = [1e8, 1e8, 2e8, 2e8, 2e8]
    closes = [4.0, 4.0, 4.0, 2.0, 2.0]  # the share table moves first, the price the next day
    flows = fund_flows(*_fund("510300", shares, closes), SESSIONS)
    assert flows["event"].tolist()[2] == "split"
    assert flows["flow"].iloc[3] == pytest.approx(0.0)


def test_big_changes_without_prices_are_not_counted_and_real_ones_are() -> None:
    frame, _ = _fund("510300", [1e8, 1e8, 3e8, 3e8], [None] * 4)
    assert fund_flows(frame, pd.DataFrame(columns=["trade_date", "symbol", "close"]), SESSIONS)["event"].tolist() == \
        ["new", None, "jump", None]
    # Shares double with a flat price: that is money, not a split.
    flows = fund_flows(*_fund("510300", [1e8, 1e8, 2e8], [4.0, 4.0, 4.0]), SESSIONS)
    assert flows["event"].tolist()[2] is None and flows["flow"].iloc[2] == pytest.approx(4e8)
    # A record after more than five sessions: the change is not attributed to one day.
    frame = pd.DataFrame({"trade_date": [SESSIONS[0], SESSIONS[1], SESSIONS[9]], "symbol": "510300",
                          "shares": [1e8, 1e8, 1.1e8]})
    prices = pd.DataFrame({"trade_date": SESSIONS[:10], "symbol": "510300", "close": 4.0})
    assert fund_flows(frame, prices, SESSIONS)["event"].tolist() == ["new", None, "gap"]


def test_abnormal_days_need_both_a_high_score_and_a_large_share() -> None:
    n = 300
    noise = [((i * 7919) % 11 - 5) * 1e5 for i in range(n)]  # small flows of up to 5e5 shares
    shares, level = [], 1e9
    for i in range(n):
        level += noise[i] + (4e7 if i == 280 else 0) - (3e7 if i == 290 else 0) + (3e6 if i == 285 else 0)
        shares.append(level)
    flows = fund_flows(*_fund("510300", shares, [4.0] * n), SESSIONS)
    daily = group_flows(flows)
    flagged = daily[daily["abnormal"].notna()]
    assert flagged["trade_date"].tolist() == [SESSIONS[280], SESSIONS[290]]
    assert flagged["abnormal"].tolist() == ["in", "out"]
    assert flagged["strong"].tolist() == [True, True]  # 4% and 3% of the group's size, z far above 8
    # Day 285 is unusual (z far above 4) but only 0.3% of the group's size.
    assert daily.loc[285, "z"] > 4 and daily.loc[285, "abnormal"] is None


def _catalog(path: Path) -> Path:
    database = path / "market.duckdb"
    days = SESSIONS[:120]
    master = pd.DataFrame([
        {"exchange": "SSE", "symbol": "510300", "name": "300ETF", "index_code": "000300", "index_name": "沪深300",
         "list_date": date(2012, 5, 28), "listed_run_id": "r2"},
        {"exchange": "SZSE", "symbol": "159919", "name": "沪深300ETF嘉实", "index_code": "399300",
         "index_name": "沪深300", "list_date": None, "listed_run_id": "r2"},
        {"exchange": "SSE", "symbol": "510050", "name": "50ETF", "index_code": "000016", "index_name": "上证50",
         "list_date": date(2005, 2, 23), "listed_run_id": "r2"},
        {"exchange": "SSE", "symbol": "561990", "name": "300增强", "index_code": "000300", "index_name": "沪深300",
         "list_date": date(2023, 1, 1), "listed_run_id": "r2"},
    ])
    shares = []
    for symbol, exchange in (("510300", "SSE"), ("159919", "SZSE"), ("510050", "SSE"), ("561990", "SSE")):
        for i, day in enumerate(days):
            if exchange == "SZSE" and day == days[-1]:
                continue  # SZSE is a day late
            shares.append({"trade_date": day, "exchange": exchange, "symbol": symbol,
                           "shares": 1e9 + 1e6 * i + (2e8 if (symbol == "510300" and i >= 110) else 0)})
    bars = [{"trade_date": day, "symbol": s, "close": 4.0} for s in ("510300", "159919", "510050", "561990")
            for day in days]
    with duckdb.connect(str(database)) as con:
        for name, frame in (("etf_master", master), ("etf_shares", pd.DataFrame(shares)),
                            ("etf_bars", pd.DataFrame(bars)), ("trading_calendar", pd.DataFrame({"trade_date": days}))):
            con.register("frame", frame)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
            con.unregister("frame")
    return database


def test_queries_group_funds_and_stop_at_the_last_complete_day(tmp_path) -> None:
    queries = EtfQueries(_catalog(tmp_path), REPO / "configs" / "etf" / "broad_groups.json")
    overview = queries.overview()
    assert overview["as_of"] == SESSIONS[118]  # SZSE has no data for the last day yet
    groups = {row["id"]: row for row in overview["groups"]}
    assert groups["csi300"]["funds"] == 2  # the enhanced 561990 is left out
    assert groups["csi300"]["flow_1d"] == pytest.approx(2 * 1e6 * 4.0)
    assert groups["all"]["funds"] == 3
    assert groups["csi300"]["last_abnormal"]["trade_date"] == SESSIONS[110].isoformat()
    series = queries.series("csi300")
    assert series["rows"][-1]["partial"] is True and series["rows"][-1]["z"] is None
    funds = queries.funds("csi300", SESSIONS[110])
    assert [row["symbol"] for row in funds["rows"]] == ["510300", "159919"]
    assert funds["rows"][0]["flow"] == pytest.approx((2e8 + 1e6) * 4.0)
    with pytest.raises(KeyError):
        queries.series("nope")


def test_a_catalog_without_etf_tables_is_unavailable(tmp_path) -> None:
    database = tmp_path / "market.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE trading_calendar AS SELECT DATE '2026-01-05' AS trade_date")
    assert not EtfQueries(database, REPO / "configs" / "etf" / "broad_groups.json").available()


def test_api_serves_the_dashboard_to_market_viewers(env, tmp_path) -> None:
    from test_api import add_user, auth, login

    _, _, client, sessions = env
    groups = REPO / "configs" / "etf" / "broad_groups.json"
    client.app.state.etf = EtfQueries(_catalog(tmp_path), groups)
    add_user(sessions, "ann", ["viewer"])
    headers = auth(login(client, "ann"))
    assert client.get("/api/v1/etf/overview").status_code == 401
    overview = client.get("/api/v1/etf/overview", headers=headers).json()
    assert overview["as_of"] == SESSIONS[118].isoformat()
    series = client.get("/api/v1/etf/groups/csi300/series", headers=headers).json()
    assert series["group"]["chart_symbol"] == "sh000300" and len(series["rows"]) == 120
    funds = client.get("/api/v1/etf/groups/csi300/funds", headers=headers, params={"day": SESSIONS[110].isoformat()})
    assert [row["symbol"] for row in funds.json()["rows"]] == ["510300", "159919"]
    assert client.get("/api/v1/etf/groups/nope/series", headers=headers).status_code == 404
    client.app.state.etf = EtfQueries(tmp_path / "missing.duckdb", groups)
    assert client.get("/api/v1/etf/overview", headers=headers).status_code == 503


def test_index_charts_get_the_abnormal_days_of_their_groups(tmp_path) -> None:
    queries = EtfQueries(_catalog(tmp_path), REPO / "configs" / "etf" / "broad_groups.json")
    marks = queries.marks("sh000300")
    assert [(m["trade_date"], m["abnormal"], m["group"]) for m in marks] == [(SESSIONS[110].isoformat(), "in", "沪深300")]
    assert queries.marks("sh000016") == [] and queries.marks("sz399001") == []
