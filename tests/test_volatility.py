"""A-share volatility indices (monitor/volatility.py): the VIX method on synthetic chains with a known volatility,
term selection, Sina parsing, the monitor's files and alert, the daily history, and the API."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from quant_system.monitor.volatility import (
    IndexState,
    SinaChains,
    VolConfig,
    VolIndex,
    VolatilityMonitor,
    daily_history,
    index_value,
    parse_sse_quotes,
    pick_terms,
    term_variance,
    years_to,
)

ETF300 = VolIndex("etf300", "300ETF 波动率", "SSE", "510300")
CONFIG = VolConfig(indices=(ETF300,))


def bs(spot: float, strike: float, t: float, vol: float, r: float, kind: str) -> float:
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    if kind == "call":
        return spot * n(d1) - strike * math.exp(-r * t) * n(d2)
    return strike * math.exp(-r * t) * n(-d2) - spot * n(-d1)


def chain(spot: float, t: float, vol: float, strikes: np.ndarray, r: float = 0.015, spread: float = 0.0) -> pd.DataFrame:
    rows = []
    for k in strikes:
        for kind in ("call", "put"):
            p = bs(spot, k, t, vol, r, kind)
            rows.append({"strike": k, "kind": kind, "bid": p * (1 - spread), "ask": p * (1 + spread)})
    return pd.DataFrame(rows)


def test_a_flat_volatility_comes_back_as_the_index() -> None:
    now = datetime(2026, 11, 5, 14, 30)
    near, nxt = date(2026, 11, 25), date(2026, 12, 23)
    strikes = np.round(np.arange(2.0, 6.5, 0.05), 3)
    chains = {e: chain(4.2, years_to(e, now), 0.20, strikes) for e in (near, nxt)}
    row = index_value(chains, now, CONFIG)
    assert row["value"] == pytest.approx(20.0, abs=0.3)
    assert row["near_vol"] == pytest.approx(20.0, abs=0.3) and row["forward"] == pytest.approx(4.2 * math.exp(0.015 * years_to(near, now)), rel=1e-3)
    # a higher implied volatility on the far term pulls the 30-day value between the two
    chains[nxt] = chain(4.2, years_to(nxt, now), 0.30, strikes)
    row = index_value(chains, now, CONFIG)
    assert 20.5 < row["value"] < 29.5 and row["next_vol"] == pytest.approx(30.0, abs=0.5)


def test_strikes_stop_after_two_missing_quotes_and_thin_chains_give_nothing() -> None:
    t = 20 / 365
    strikes = np.round(np.arange(3.0, 5.6, 0.1), 2)
    full = chain(4.2, t, 0.2, strikes)
    gap = full.copy()
    gap.loc[(gap["kind"] == "put") & gap["strike"].isin([3.6, 3.7]), ["bid", "ask"]] = 0.0  # two in a row unquoted
    used_full = term_variance(full, t, 0.015)["strikes"]
    used_gap = term_variance(gap, t, 0.015)["strikes"]
    assert used_gap < used_full - 2  # the puts below the gap are not used
    thin = full[full["strike"].isin([4.2])]
    assert term_variance(thin, t, 0.015) is None


def test_the_near_term_rolls_when_closer_than_min_days() -> None:
    expiries = [date(2026, 11, 25), date(2026, 12, 23), date(2027, 3, 24)]
    assert pick_terms(expiries, datetime(2026, 11, 5, 10), 7) == expiries[:2]
    assert pick_terms(expiries, datetime(2026, 11, 20, 10), 7) == expiries[1:]


SINA = ('var hq_str_CON_OP_10012457="2,0.0102,0.0103,0.0103,81,49324,-6.36,4.4000,0.0110,0.0090,0.4060,0.0001,'
        + ",".join(["0"] * 20) + ',2026-11-05 15:00:00,0,E 00,EBS,510300,300ETF购11月4400,50.91,0.0143,0.0087,42333,1";\n'
        'var hq_str_CON_OP_10011999="1,0.2,0.21,0.22,1,1,0,2.7550,0,0,0,0,' + ",".join(["0"] * 20)
        + ',2026-11-05 15:00:00,0,E 00,EBS,510050,50ETF购12月2755A,0,0,0,0,0";\n')


def test_sina_quotes_parse_and_adjusted_contracts_are_left_out() -> None:
    quotes = parse_sse_quotes(SINA)
    assert quotes["contract"].tolist() == ["10012457"]
    row = quotes.iloc[0]
    assert (row["strike"], row["bid"], row["ask"], row["time"]) == (4.4, 0.0102, 0.0103, "2026-11-05 15:00:00")


class FakeChains(SinaChains):
    """Chains of a flat volatility that jumps after ``jump_at`` polls."""

    def __init__(self) -> None:
        super().__init__(lambda url: "")
        self.vol, self.calls = 0.20, 0
        self.now = datetime(2026, 11, 5, 10, 0)

    def chain(self, index: VolIndex, month: str) -> pd.DataFrame:
        self.calls += 1
        expiry = date(2026, 11, 25) if month == "2026-11" else date(2026, 12, 23)
        return chain(4.2, years_to(expiry, self.now), self.vol, np.round(np.arange(2.0, 6.5, 0.05), 3))


def test_the_monitor_writes_minutes_and_alerts_once_on_a_spike(tmp_path: Path) -> None:
    fake = FakeChains()
    alerts = []
    state = IndexState(ETF300, [("2026-11", date(2026, 11, 25)), ("2026-12", date(2026, 12, 23))], previous_close=20.0,
                       reference=pd.Series(np.linspace(12, 30, 200)))
    monitor = VolatilityMonitor(tmp_path, CONFIG, date(2026, 11, 5), [state], fake, lambda: fake.now,
                                lambda index, row, text: alerts.append(text))
    monitor.poll()
    assert not alerts
    fake.vol, fake.now = 0.27, fake.now + timedelta(minutes=1)   # +35%, above the 80th percentile of 12..30
    monitor.poll()
    fake.now += timedelta(minutes=1)
    monitor.poll()
    assert len(alerts) == 1 and "300ETF 波动率" in alerts[0][0] and "+3" in alerts[0][1]
    out = tmp_path / "data" / "monitor" / "volatility" / "2026-11-05"
    minutes = pd.read_parquet(out / "minutes.parquet")
    assert minutes["time"].tolist() == ["10:00", "10:01", "10:02"] and minutes["value"].iloc[0] == pytest.approx(20, abs=0.3)
    status = json.loads((out / "status.json").read_text(encoding="utf-8"))
    assert status["alerts"] == [{"id": "etf300", "time": "10:01"}] and status["indices"][0]["previous_close"] == 20.0


def test_the_daily_history_uses_closes_and_leaves_adjusted_contracts_out(tmp_path: Path) -> None:
    days = [date(2026, 10, 28), date(2026, 10, 29)]
    contracts, bars = [], []
    strikes = np.round(np.arange(3.0, 5.6, 0.05), 3)
    for e, month in ((date(2026, 11, 25), "2026-11"), (date(2026, 12, 23), "2026-12")):
        for k in strikes:
            for kind in ("call", "put"):
                cid = f"{month}{kind}{k}"
                contracts.append({"contract": cid, "exchange": "SSE", "underlying": "510300", "kind": kind,
                                  "expiry_month": month, "expiry_date": e, "strike": k, "adjusted": False})
                for d, vol in zip(days, (0.18, 0.24)):
                    t = years_to(e, datetime.combine(d, datetime.min.time()).replace(hour=15))
                    bars.append({"contract": cid, "trade_date": d, "close": bs(4.2, k, t, vol, 0.015, kind), "settle": None})
    contracts.append({"contract": "adj", "exchange": "SSE", "underlying": "510300", "kind": "call", "expiry_month": "2026-11",
                      "expiry_date": date(2026, 11, 25), "strike": 4.2, "adjusted": True})
    bars.append({"contract": "adj", "trade_date": days[0], "close": 9.9, "settle": None})
    c, b = pd.DataFrame(contracts), pd.DataFrame(bars)  # noqa: F841 (read by DuckDB)
    con = duckdb.connect()
    con.execute("CREATE TABLE option_contracts AS SELECT * FROM c")
    con.execute("CREATE TABLE option_daily_bars AS SELECT * FROM b")
    history = daily_history(con, CONFIG)
    assert history["trade_date"].tolist() == days
    assert history["value"].tolist() == pytest.approx([18.0, 24.0], abs=0.4)


def test_the_page_query_gives_the_latest_value_its_change_and_percentile(tmp_path: Path) -> None:
    from quant_system.app.monitor import VolatilityMissing, VolatilityQueries

    queries = VolatilityQueries(tmp_path)
    with pytest.raises(VolatilityMissing):
        queries.day()
    base = tmp_path / "data" / "monitor" / "volatility"
    base.mkdir(parents=True)
    days = pd.bdate_range(end="2026-11-04", periods=300).date
    pd.DataFrame({"id": "etf300", "trade_date": days, "value": np.linspace(15, 25, len(days))}).to_parquet(
        base / "daily.parquet", index=False)
    fake = FakeChains()
    state = IndexState(ETF300, [("2026-11", date(2026, 11, 25)), ("2026-12", date(2026, 12, 23))], previous_close=25.0)
    monitor = VolatilityMonitor(tmp_path, CONFIG, date(2026, 11, 5), [state], fake, lambda: fake.now)
    monitor.poll()
    result = queries.day(now=datetime(2026, 11, 5, 10, 1))
    assert result["day"] == "2026-11-05" and result["live"]
    [entry] = result["indices"]
    assert entry["value"] == pytest.approx(20, abs=0.3) and entry["previous_close"] == 25.0
    assert entry["change"] == pytest.approx(-0.2, abs=0.02)
    assert entry["percentile"] == pytest.approx(0.5, abs=0.05)          # 20 is the middle of 15..25
    assert len(entry["series"]) == 1 and len(entry["history"]) == 300
    with pytest.raises(VolatilityMissing):
        queries.day("2026-11-04")


def test_the_endpoint_needs_a_login_and_reports_missing_data(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from quant_system.app.db import open_database
    from quant_system.app.main import create_app
    from quant_system.app.settings import AppSettings
    from test_api import add_user, auth, login
    from test_decision import make_root

    root = make_root(tmp_path / "root")
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    alice = auth(login(client, "alice"))
    assert client.get("/api/v1/monitor/volatility").status_code == 401
    missing = client.get("/api/v1/monitor/volatility", headers=alice)
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_volatility_data"


def test_the_backfill_replays_stored_minutes_of_the_sse_options() -> None:
    from quant_system.monitor.volatility import replay_minutes

    day = date(2026, 11, 5)
    strikes = np.round(np.arange(3.0, 5.6, 0.05), 3)
    contracts, minutes = [], []
    for e, month in ((date(2026, 11, 25), "2026-11"), (date(2026, 12, 23), "2026-12")):
        for k in strikes:
            for kind in ("call", "put"):
                cid = f"{month}{kind}{k}"
                contracts.append({"contract": cid, "exchange": "SSE", "underlying": "510300", "kind": kind,
                                  "strike": k, "expiry_date": e, "adjusted": False})
                for clock, vol in (("09:31:00", 0.20), ("09:32:00", 0.25)):
                    t = years_to(e, datetime.combine(day, time.fromisoformat(clock)))
                    minutes.append({"contract": cid, "time": clock, "price": bs(4.2, k, t, vol, 0.015, kind)})
    out = replay_minutes(pd.DataFrame(minutes), pd.DataFrame(contracts), CONFIG, day)
    assert out["time"].tolist() == ["09:31", "09:32"]
    assert out["value"].tolist() == pytest.approx([20.0, 25.0], abs=0.4)


def test_every_configured_index_is_listed_even_without_its_minutes(tmp_path: Path) -> None:
    from quant_system.app.monitor import VolatilityQueries

    (tmp_path / "configs" / "monitor").mkdir(parents=True)
    (tmp_path / "configs" / "monitor" / "volatility.json").write_text(json.dumps({"indices": [
        {"id": "etf300", "name": "300ETF 波动率", "exchange": "SSE", "underlying": "510300"},
        {"id": "io", "name": "沪深300 股指期权波动率", "exchange": "CFFEX", "underlying": "IO"}]}), encoding="utf-8")
    base = tmp_path / "data" / "monitor" / "volatility"
    base.mkdir(parents=True)
    days = list(pd.bdate_range(end="2026-11-04", periods=10).date)
    pd.DataFrame({"id": ["etf300"] * 10 + ["io"] * 10, "trade_date": days * 2,
                  "value": list(range(10, 20)) + list(range(30, 40))}).to_parquet(base / "daily.parquet", index=False)
    fake = FakeChains()
    state = IndexState(ETF300, [("2026-11", date(2026, 11, 25)), ("2026-12", date(2026, 12, 23))])
    VolatilityMonitor(tmp_path, CONFIG, date(2026, 11, 3), [state], fake, lambda: fake.now).poll()
    result = VolatilityQueries(tmp_path).day()
    assert [i["id"] for i in result["indices"]] == ["etf300", "io"]
    io = result["indices"][1]
    assert io["name"] == "沪深300 股指期权波动率" and io["series"] == []
    assert io["value"] == 38 and io["previous_close"] == 37   # its daily value of 11-03, and 11-02's


def test_the_monitor_starts_without_the_catalog_and_takes_a_newer_history(tmp_path: Path) -> None:
    """The months come from the canonical contracts (the 09:05 catch-up may hold the catalog's lock at 09:20), and
    the previous close follows the daily history when the catch-up recomputes it after the monitor started."""
    import os

    from quant_system.data_platform.storage import canonical_path
    from quant_system.monitor.volatility import prepare

    path = canonical_path(tmp_path, "option_contracts")
    path.parent.mkdir(parents=True)
    rows = [{"contract": f"{u}{m}{k}", "exchange": "SSE", "underlying": u, "kind": "call", "expiry_month": m,
             "expiry_date": e, "strike": 4.2, "adjusted": False}
            for u in ("510300", "510050") for k in "ab"
            for m, e in (("2026-10", "2026-10-28"), ("2026-12", "2026-12-23"), ("2026-11", "2026-11-25"))]
    pd.DataFrame(rows).to_parquet(path, index=False)
    base = tmp_path / "data" / "monitor" / "volatility"
    base.mkdir(parents=True)
    days = list(pd.bdate_range(end="2026-11-03", periods=30).date)
    values = list(np.linspace(15, 20, 30))
    pd.DataFrame({"id": "etf300", "trade_date": days, "value": values}).to_parquet(base / "daily.parquet", index=False)
    assert not (tmp_path / "data" / "market.duckdb").exists()
    [state] = prepare(tmp_path, CONFIG, date(2026, 11, 5))
    assert state.months == [("2026-11", date(2026, 11, 25)), ("2026-12", date(2026, 12, 23))]
    assert state.previous_close == 20 and len(state.reference) == 30

    fake = FakeChains()
    monitor = VolatilityMonitor(tmp_path, CONFIG, date(2026, 11, 5), [state], fake, lambda: fake.now)
    monitor.poll()
    history = base / "daily.parquet"
    stamp = history.stat().st_mtime_ns
    pd.DataFrame({"id": "etf300", "trade_date": days + [date(2026, 11, 4)], "value": values + [25.0]}).to_parquet(
        history, index=False)
    os.utime(history, ns=(stamp + 10**9, stamp + 10**9))
    fake.now += timedelta(minutes=1)
    monitor.poll()
    assert state.previous_close == 25 and len(state.reference) == 31
    status = json.loads((base / "2026-11-05" / "status.json").read_text(encoding="utf-8"))
    assert status["indices"][0]["previous_close"] == 25.0
