"""Money map (docs/design/money-map.md): crowding, zones, point-in-time statistics, queries and API."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from quant_system.analytics.money_map import (
    CROWDED,
    HORIZONS,
    cell_of,
    crossings,
    daily_measures,
    entries,
    pit_stats,
    stats_at,
    weekly_table,
    zone_of,
)
from quant_system.app.money_map import MoneyMapMissing, MoneyMapQueries
from quant_system.data_platform.sw_index import SW_L1

CROWDED_CODE = "801780"  # 银行: its turnover triples over the last 30 sessions


def sw_panel(days: pd.DatetimeIndex, codes=tuple(SW_L1), seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (len(days), len(codes))), axis=0)),
                         index=days, columns=list(codes))
    amount = pd.DataFrame(rng.uniform(0.8, 1.2, (len(days), len(codes))) * 1e10, index=days, columns=list(codes))
    return close, amount


def test_crowding_is_the_recent_share_over_its_long_mean() -> None:
    days = pd.bdate_range("2020-01-02", periods=800)
    amount = pd.DataFrame(1.0, index=days, columns=["A", "B", "C"])
    amount.iloc[-20:, 0] = 2.0  # A's share goes from 1/3 to 1/2 in the last 20 sessions
    close = pd.DataFrame(100.0, index=days, columns=["A", "B", "C"])
    c = daily_measures(close, amount)["c"]
    long_mean = (730 / 3 + 20 * 0.5) / 750
    assert c["A"].iloc[-1] == pytest.approx(0.5 / long_mean)
    assert c["B"].iloc[-1] == pytest.approx(0.25 / ((730 / 3 + 20 * 0.25) / 750))
    assert c["A"].iloc[:249].isna().all() and c["A"].iloc[260] == pytest.approx(1.0)  # shown from 250 sessions


def test_zones_and_cells_follow_the_map() -> None:
    c = np.array([2.0, 1.0, 0.7, 0.5, 1.6, np.nan])
    r12 = np.array([0.1, 0.4, 0.2, 0.05, 0.6, 0.1])
    assert zone_of(c, r12).tolist() == ["crowded", "fight", "starting", "waiting", "other", None]
    assert cell_of(c, r12).tolist() == ["C>1.8|<15%", "C 0.8–1.4|30–50%", "C<0.8|15–30%", "C<0.8|<15%",
                                        "C 1.4–1.8|>50%", None]


def test_crossings_remind_once_and_relieve_only_after_crowding() -> None:
    days = list(pd.bdate_range("2026-01-05", periods=8).date)
    c = np.array([np.nan, 1.3, 1.9, 2.0, 1.5, 1.3, 1.2, 1.85])
    assert crossings(days, c, 1.8, 1.4) == [(days[2], "crowded", 1.9), (days[5], "relief", 1.3),
                                           (days[7], "crowded", 1.85)]
    assert crossings(days, np.array([np.nan, 1.3, 1.2, 1.1, 1.0, 0.9, 0.8, 0.7]), 1.8, 1.4) == []


def test_entries_need_a_gap_since_the_last_crowded_week() -> None:
    weeks = [0, 1, 2, 20, 30, 50]
    table = pd.DataFrame({"code": "A", "week_no": weeks, "c": [1.9, 2.0, 1.9, 1.85, 1.9, 1.95]})
    assert entries(table)["week_no"].tolist() == [0, 20, 50]


def test_statistics_at_a_week_use_only_what_was_known_then() -> None:
    days = pd.bdate_range("2019-01-02", periods=1300)
    close, amount = sw_panel(days, codes=("A", "B", "C", "D", "E"))
    amount.iloc[900:960, 0] *= 3  # some crowded weeks in the middle
    bench = close.mean(axis=1)
    table = weekly_table(close, amount, bench)
    weeks = int(table["week_no"].max()) + 1
    cutoff_week = 200
    cutoff_day = table.loc[table["week_no"] == cutoff_week, "week"].iloc[0]
    cut = weekly_table(close.loc[:cutoff_day], amount.loc[:cutoff_day], bench.loc[:cutoff_day])
    for key in ("cell", "zone"):
        full, short = pit_stats(table, key, weeks), pit_stats(cut, key, cutoff_week + 1)
        for value in table[key].dropna().unique():
            assert stats_at(full, cutoff_week, value) == stats_at(short, cutoff_week, value), (key, value)
    # Forward returns of the last weeks are unknown, not made up.
    last = table[table["week_no"] == weeks - 1]
    assert last[[f"x{h}" for h in HORIZONS]].isna().all().all()


def money_map_db(path: Path, days: pd.DatetimeIndex) -> None:
    close, amount = sw_panel(days)
    amount.iloc[-30:, list(SW_L1).index(CROWDED_CODE)] *= 3
    rows = []
    for code in SW_L1:
        rows.append(pd.DataFrame({"symbol": code, "name": SW_L1[code], "trade_date": days.date,
                                  "close": close[code].to_numpy(), "turnover_cny": amount[code].to_numpy()}))
    bars = pd.concat(rows, ignore_index=True)
    index = pd.DataFrame({"symbol": "sh000300", "trade_date": days.date, "close": close.mean(axis=1).to_numpy()})
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE sw_index_bars AS SELECT * FROM bars")
        con.execute("CREATE TABLE index_bars AS SELECT * FROM index")


def test_queries_give_the_replay_frames_an_industry_and_its_reminders(tmp_path: Path) -> None:
    days = pd.bdate_range("2022-06-01", periods=900)
    path = tmp_path / "market.duckdb"
    money_map_db(path, days)
    queries = MoneyMapQueries(path)
    overview = queries.overview(10)
    assert len(overview["frames"]) == 10 and overview["as_of"] == days[-1].date()
    last = overview["frames"][-1]
    assert last["week"] == days[-1].date() and len(last["rows"]) == 31
    crowded = next(r for r in last["rows"] if r["code"] == CROWDED_CODE)
    assert crowded["c"] > CROWDED and crowded["zone"] == "crowded"
    assert set(last["cells"]) == {r["cell"] for r in last["rows"]}
    json.dumps(overview, default=str)  # the API can serve it
    assert len(queries.overview(0)["frames"]) == overview["weeks_total"]

    detail = queries.industry(CROWDED_CODE)
    assert detail["name"] == "银行" and len(detail["dates"]) == len(detail["c"]) == len(detail["relative"])
    with pytest.raises(KeyError):
        queries.industry("999999")

    state = queries.crowding(CROWDED_CODE, 1.8, 1.4)
    assert state["zone"] == "crowded" and state["events"][-1][1] == "crowded"
    assert state["events"][-1][0] >= days[-30].date()
    assert queries.crowding(CROWDED_CODE, 5.0, 1.4)["events"] == []  # a higher line is never crossed


def test_a_catalog_without_sw_indices_is_reported_missing(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE index_bars (symbol VARCHAR, trade_date DATE, close DOUBLE)")
    queries = MoneyMapQueries(path)
    assert not queries.available()
    with pytest.raises(MoneyMapMissing):
        queries.overview()
    with pytest.raises(MoneyMapMissing):
        queries.crowding("801780")


# -- API and the position manager ------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database  # noqa: E402
from quant_system.app.db.models import PmSettings, PmSignal  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from test_api import add_user, auth, login  # noqa: E402
from test_api_pm import DAYS, add, market_db  # noqa: E402
from test_decision import make_root  # noqa: E402


@pytest.fixture
def pm_map(tmp_path: Path):
    root = make_root(tmp_path / "root")
    market_db(root)
    path = root / "data" / "market.duckdb"
    days = pd.bdate_range(end=DAYS[-1], periods=900)
    close, amount = sw_panel(days)
    amount.iloc[-12:, list(SW_L1).index(CROWDED_CODE)] *= 3  # 银行 turns crowded in the last weeks
    bars = pd.concat([pd.DataFrame({"symbol": code, "name": SW_L1[code], "trade_date": days.date,
                                    "close": close[code].to_numpy(), "turnover_cny": amount[code].to_numpy()})
                      for code in SW_L1], ignore_index=True)
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE sw_index_bars AS SELECT * FROM bars")
        con.execute("CREATE TABLE industry_sw (symbol VARCHAR, start_date DATE, end_date DATE, l1_name VARCHAR)")
        con.execute("INSERT INTO industry_sw VALUES ('600000', DATE '2021-07-30', NULL, '银行')")
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    return client, sessions, auth(login(client, "alice"))


def test_the_api_serves_the_map_and_an_industry(pm_map) -> None:
    client, _, alice = pm_map
    overview = client.get("/api/v1/moneymap?weeks=4", headers=alice)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert len(body["frames"]) == 4 and body["rules"]["crowded"] == CROWDED
    assert client.get(f"/api/v1/moneymap/industries/{CROWDED_CODE}", headers=alice).json()["name"] == "银行"
    assert client.get("/api/v1/moneymap/industries/999999", headers=alice).status_code == 404
    assert client.get("/api/v1/moneymap").status_code == 401


def test_the_position_manager_reminds_when_the_industry_turns_crowded(pm_map) -> None:
    client, sessions, alice = pm_map
    add(client, alice, "600000")
    client.get("/api/v1/pm/settings", headers=alice)  # creates the settings row
    with sessions() as session:  # last evaluated before the industry crossed 1.8
        session.scalar(select(PmSettings)).evaluated_through = DAYS[-15].date()
        session.commit()
    assert client.get("/api/v1/pm/board", headers=alice).status_code == 200
    with sessions() as session:
        signals = session.scalars(select(PmSignal).where(PmSignal.rule == "industry_crowded")).all()
        assert len(signals) == 1 and "银行" in signals[0].message and signals[0].priority == 4  # not holding
    assert client.get("/api/v1/pm/board", headers=alice).json()["new_signals"] == 0  # stored once
    items = client.get("/api/v1/pm/items", headers=alice).json()["items"]
    industry = next(i for i in items if i["symbol"] == "600000")["industry"]
    assert industry["code"] == CROWDED_CODE and industry["zone"] == "crowded" and industry["c"] > 1.8


def test_the_reminder_lines_are_settings(pm_map) -> None:
    client, _, alice = pm_map
    settings = client.get("/api/v1/pm/settings", headers=alice).json()
    assert (settings["crowd_high"], settings["crowd_low"]) == (1.8, 1.4)
    changed = client.put("/api/v1/pm/settings", json={"crowd_high": 2.0, "crowd_low": 1.2}, headers=alice)
    assert changed.status_code == 200 and (changed.json()["crowd_high"], changed.json()["crowd_low"]) == (2.0, 1.2)
    assert client.put("/api/v1/pm/settings", json={"crowd_high": 1.0, "crowd_low": 1.2},
                      headers=alice).status_code == 400
