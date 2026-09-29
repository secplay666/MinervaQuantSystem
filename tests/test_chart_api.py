"""Stock chart backend: weekly/monthly bars, chart marks, per-user drawings."""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("fastapi")

from quant_system.app.market import aggregate_bars  # noqa: E402
from test_api import add_user, auth, env, login  # noqa: E402,F401  (fixtures)


def daily(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"trade_date": d, "open": o, "high": h, "low": low, "close": c, "volume": 100.0,
                          "amount": 1000.0, "turnover_rate": 0.5, "hfq_close": c * 2} for d, o, h, low, c in
                         [(r[0], r[1], r[2], r[3], r[4]) for r in rows]])


def test_weekly_and_monthly_bars_are_aggregated_by_calendar() -> None:
    frame = daily([
        ("2026-09-24", 10, 11, 9.5, 10.5, 0), ("2026-09-25", 10.5, 12, 10, 11.5, 0),  # Thu, Fri
        ("2026-09-28", 11.5, 11.8, 10.8, 11, 0), ("2026-09-30", 11, 11.2, 10.5, 10.6, 0),  # Mon, Wed
        ("2026-10-08", 10.6, 10.9, 10.1, 10.8, 0),  # after the holiday
    ])
    weeks = aggregate_bars(frame, "week", with_turnover_rate=True)
    assert list(weeks["trade_date"]) == ["2026-09-25", "2026-09-30", "2026-10-08"]  # dated by the last session
    first = weeks.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (10, 12, 9.5, 11.5)
    assert first["volume"] == 200 and first["turnover_rate"] == 1.0
    assert weeks.iloc[1]["pct_change"] == pytest.approx((10.6 / 11.5 - 1) * 100)  # on hfq closes
    months = aggregate_bars(frame, "month")
    assert list(months["trade_date"]) == ["2026-09-30", "2026-10-08"]
    assert months.iloc[0]["high"] == 12 and months.iloc[0]["low"] == 9.5


def test_drawings_are_per_user_and_marks_show_fills_only_with_account_access(env) -> None:
    _, _, client, sessions = env
    add_user(sessions, "ann", ["viewer"])
    add_user(sessions, "bob", ["viewer"])
    add_user(sessions, "carl", ["viewer"])
    ann, bob = auth(login(client, "ann")), auth(login(client, "bob"))
    line = {"name": "segment", "points": [{"timestamp": 1, "value": 10.0}, {"timestamp": 2, "value": 11.0}]}
    assert client.get("/api/v1/charts/600000/drawings", headers=ann).json()["overlays"] == []
    saved = client.put("/api/v1/charts/600000/drawings", headers=ann, json={"overlays": [line]})
    assert saved.status_code == 200 and saved.json()["count"] == 1
    assert client.get("/api/v1/charts/600000/drawings", headers=ann).json()["overlays"] == [line]
    assert client.get("/api/v1/charts/600000/drawings", headers=bob).json()["overlays"] == []  # not shared
    client.put("/api/v1/charts/600000/drawings", headers=ann, json={"overlays": []})
    assert client.get("/api/v1/charts/600000/drawings", headers=ann).json()["overlays"] == []
    huge = {"name": "brush", "points": [{"timestamp": k, "value": 1.0} for k in range(10_000)]}
    assert client.put("/api/v1/charts/600000/drawings", headers=ann, json={"overlays": [huge]}).status_code == 400
    assert client.put("/api/v1/charts/..%2F/drawings", headers=ann, json={"overlays": []}).status_code in (400, 404)

    marks = client.get("/api/v1/instruments/600000/marks", headers=ann).json()
    assert set(marks) == {"dividends", "fills", "reports", "risk", "suspensions"} and marks["fills"] == []
    assert client.get("/api/v1/instruments/x/marks", headers=ann).status_code == 404


def test_search_finds_indices_before_stocks(env) -> None:
    import duckdb

    root, _, client, sessions = env
    add_user(sessions, "dora", ["viewer"])
    viewer = auth(login(client, "dora"))
    assert [r["symbol"] for r in client.get("/api/v1/instruments/search", headers=viewer,
                                            params={"q": "600000"}).json()] == ["600000"]  # no index table yet
    with duckdb.connect(str(root / "data" / "market.duckdb")) as con:
        con.execute("CREATE TABLE index_bars (symbol VARCHAR, name VARCHAR, trade_date DATE, close DOUBLE)")
        con.execute("INSERT INTO index_bars VALUES ('sh000001', '上证指数', DATE '2026-09-24', 3888.37), "
                    "('H00300', '沪深300全收益', DATE '2026-09-24', 6000.0)")
    found = client.get("/api/v1/instruments/search", headers=viewer, params={"q": "上证"}).json()
    assert found[0] == {"symbol": "sh000001", "name": "上证指数", "board": "INDEX", "delist_date": None}
    assert [i["symbol"] for i in client.get("/api/v1/market/indices", headers=viewer).json()] == ["H00300", "sh000001"]
