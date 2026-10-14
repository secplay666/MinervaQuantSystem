"""The options page's queries and API (app/options.py, app/api/options.py) on a catalog built by the options step."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from fakes import OPEN_DATES, FakeProvider, at, make_config
from quant_system.app.options import OptionQueries, OptionsMissing
from quant_system.data_platform.pipeline import IngestionPipeline
from test_api import add_user, auth, env, login  # noqa: F401  (env is a fixture)


@pytest.fixture
def options_db(tmp_path: Path) -> Path:
    """Two morning runs (09-24 and 09-26) of the fake options; 510050 closes at 2.95 and the indices at 4,000."""
    root = tmp_path / "opt"
    provider = FakeProvider(today=date(2026, 9, 23), options=True)
    for day, today in ((date(2026, 9, 24), date(2026, 9, 23)), (date(2026, 9, 26), date(2026, 9, 24))):
        provider.today = today
        IngestionPipeline(root, make_config(), provider=provider, clock=lambda d=day: at(d, 9, 5)).run(
            steps={"options"})
    path = root / "data" / "market.duckdb"
    days = [d for d in OPEN_DATES if d <= date(2026, 9, 24)]
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE etf_bars (symbol VARCHAR, trade_date DATE, close DOUBLE)")
        con.execute("CREATE TABLE index_bars (symbol VARCHAR, trade_date DATE, close DOUBLE)")
        for d in days:
            con.execute("INSERT INTO etf_bars VALUES ('510050', ?, 2.95)", [d])
            con.execute("INSERT INTO index_bars VALUES ('sh000300', ?, 3980.0), ('sh000852', ?, 7000.0)", [d, d])
    return path


def test_underlyings_list_spans_and_live_contracts(options_db: Path) -> None:
    rows = {r["underlying"]: r for r in OptionQueries(options_db).underlyings()["underlyings"]}
    assert list(rows) == ["510050", "IO", "MO"]
    sse = rows["510050"]
    assert (sse["first_date"], sse["last_date"], sse["contracts"], sse["live"], sse["minutes"]) == \
        (date(2026, 9, 1), date(2026, 9, 24), 4, 2, True)
    assert rows["IO"]["live"] == 0 and rows["IO"]["last_date"] == date(2026, 9, 18) and not rows["IO"]["minutes"]


def test_chain_defaults_to_the_nearest_live_month_and_values_against_the_underlying(options_db: Path) -> None:
    chain = OptionQueries(options_db).chain("510050")
    assert (chain["month"], chain["day"], chain["expiry_date"], chain["days_left"]) == \
        ("2026-10", date(2026, 9, 24), date(2026, 10, 28), 34)
    assert [m["expiry_month"] for m in chain["months"]] == ["2026-09", "2026-10"] and chain["months"][0]["expired"]
    assert chain["underlying"]["close"] == 2.95
    adjusted, standard = chain["rows"]
    assert (adjusted["strike"], adjusted["adjusted"], adjusted["call"], adjusted["atm"]) == (2.755, True, None, False)
    assert standard["strike"] == 2.9 and standard["atm"] and standard["put"] is None
    call = standard["call"]
    assert call["close"] == 0.117 and call["prev_close"] == 0.116 and call["iv"] == 0.18 and call["delta"] == 0.5
    assert call["volume"] == 478 and call["open_interest"] == 300  # from the minute bars
    assert call["intrinsic"] == pytest.approx(0.05) and call["time_value"] == pytest.approx(0.067)


def test_an_expired_month_shows_its_last_session_and_cffex_uses_the_exchange_delta(options_db: Path) -> None:
    queries = OptionQueries(options_db)
    september = queries.chain("510050", month="2026-09")
    assert september["day"] == date(2026, 9, 23) and september["expired"]
    (row,) = september["rows"]
    assert row["call"]["iv"] is None  # the exchange lists no risk indicators on the expiry day
    assert queries.chain("510050", month="2026-09", day=date(2026, 9, 10))["day"] == date(2026, 9, 10)
    io = queries.chain("IO")
    assert io["month"] == "2026-09" and io["day"] == date(2026, 9, 18)
    (row,) = io["rows"]
    assert row["strike"] == 4000 and row["call"]["delta"] == 0.45 and row["put"]["open_interest"] == 300
    assert row["put"]["intrinsic"] == 20.0 and row["atm"]


def test_contract_history_and_minutes(options_db: Path) -> None:
    queries = OptionQueries(options_db)
    detail = queries.contract("10009003")
    assert detail["terms"]["strike"] == 2.9 and detail["terms"]["underlying_name"] == "上证50ETF"
    assert len(detail["bars"]) == 18 and detail["bars"][-1]["iv"] == 0.18
    assert [b["volume"] for b in detail["bars"][-3:]] == [None, 478, 478]  # minutes of 09-23 and 09-24 only
    assert detail["bars"][-1]["underlying_close"] == 2.95 and detail["bars"][-1]["intrinsic"] == pytest.approx(0.05)
    assert detail["minute_days"] == [date(2026, 9, 23), date(2026, 9, 24)]
    minutes = queries.minutes("10009003", date(2026, 9, 24))
    assert len(minutes["rows"]) == 240 and minutes["prev_close"] == 0.116 and minutes["rows"][0]["price"] is None
    assert queries.minutes("10009003", date(2026, 9, 22))["rows"] == []
    assert queries.contract("MO2610-C-7000")["minute_days"] == []
    with pytest.raises(LookupError):
        queries.contract("10000000")
    with pytest.raises(LookupError):
        queries.chain("510300")


def test_a_catalog_without_options_says_so(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE trading_calendar (trade_date DATE)")
    with pytest.raises(OptionsMissing):
        OptionQueries(path).underlyings()


def test_the_api_needs_a_login_and_maps_errors(env, options_db: Path) -> None:  # noqa: F811
    _, _, client, sessions = env
    client.app.state.options = OptionQueries(options_db)
    assert client.get("/api/v1/options/underlyings").status_code == 401
    add_user(sessions, "viewer", ["viewer"])
    headers = auth(login(client, "viewer"))
    assert len(client.get("/api/v1/options/underlyings", headers=headers).json()["underlyings"]) == 3
    chain = client.get("/api/v1/options/chain", params={"underlying": "510050", "month": "2026-09"}, headers=headers)
    assert chain.status_code == 200 and chain.json()["day"] == "2026-09-23"
    assert client.get("/api/v1/options/chain", params={"underlying": "x;1"}, headers=headers).status_code == 422
    assert client.get("/api/v1/options/contracts/10000000", headers=headers).status_code == 404
    minutes = client.get("/api/v1/options/contracts/10009003/minutes", params={"day": "2026-09-24"}, headers=headers)
    assert len(minutes.json()["rows"]) == 240
