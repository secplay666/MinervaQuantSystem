"""Stage-4 account and instrument insights: industry exposure against the
target, the strategy's scores of a stock, periodic-report alerts (M10)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from quant_system.app.db import open_database  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from quant_system.decision.accounts import add_event, create_account  # noqa: E402
from test_api import add_user, auth, login, tiny_market  # noqa: E402
from test_decision import CONFIG, golden, loaded, make_root, rebalance_days, run  # noqa: E402,F401  (fixtures)


def _manual_decision(tmp_path: Path, loaded, golden):
    """A manual account holding the backtest's positions, decided on a rebalance day."""
    root = make_root(tmp_path)
    day = rebalance_days(golden)[10]
    held = golden.positions[pd.to_datetime(golden.positions["session"]).dt.date == day]
    _, factory = open_database(root / "app.sqlite")
    with factory() as session:
        create_account(session, root, account_id="m", name="m", mode="manual", strategy_config=CONFIG,
                       initial_cash_fen=50_000_000, start_date=day, actor="test")
        for row in held.itertuples():
            add_event(session, "m", day, "adjustment", {"old_quantity": 0, "new_quantity": int(row.quantity),
                      "cost_fen": 0}, event_id=f"h-{row.symbol}", actor="test", symbol=row.symbol)
        session.commit()
    (outcome,) = run(root, loaded, day)
    assert outcome.status == "complete" and outcome.kind == "rebalance"
    symbols = sorted(held["symbol"].astype(str))
    tiny_market(root, symbols)
    with duckdb.connect(str(root / "data" / "market.duckdb")) as con:
        con.execute("CREATE TABLE industry_sw (symbol VARCHAR, l1_name VARCHAR, start_date DATE, end_date DATE)")
        for k, symbol in enumerate(symbols):
            con.execute("INSERT INTO industry_sw VALUES (?, ?, DATE '2020-01-01', NULL)",
                        [symbol, "银行" if k % 2 else "电子"])
    settings = AppSettings(root=root, db_path=root / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "viewer", ["viewer"])
    return root, outcome, client, auth(login(client, "viewer")), symbols


def test_exposure_and_signals(tmp_path: Path, loaded, golden) -> None:
    root, outcome, client, viewer, held = _manual_decision(tmp_path, loaded, golden)

    exposure = client.get("/api/v1/accounts/m/exposure", headers=viewer).json()
    assert exposure["target_run_id"] == outcome.run_id
    by_name = {r["name"]: r for r in exposure["industries"]}
    assert {"银行", "电子"} <= set(by_name)
    held_weight = sum(r["weight"] for r in exposure["industries"])
    assert held_weight + exposure["cash_weight"] == pytest.approx(1.0)
    assert sum(r["target_weight"] for r in exposure["industries"]) == pytest.approx(1.0, abs=0.1)
    assert sum(r["count"] for r in exposure["industries"]) == len(held)
    assert 0 < exposure["top10_weight"] <= 1 and 1 < exposure["effective_names"] <= len(held)

    scores = pd.read_csv(Path(outcome.report_dir) / "scores.csv", dtype={"symbol": str})
    best = str(scores.iloc[0]["symbol"])
    signals = client.get(f"/api/v1/instruments/{best}/signals", headers=viewer).json()
    assert signals["run_id"] == outcome.run_id and signals["row"]["rank"] == 1
    assert signals["scored"] == int(scores["rank"].notna().sum()) and "f_value" in signals["row"]
    target = next(iter(pd.read_csv(Path(outcome.report_dir) / "targets.csv", dtype={"symbol": str})["symbol"]))
    history = client.get(f"/api/v1/instruments/{target}/signals", headers=viewer).json()["history"]
    assert history and history[0]["account_id"] == "m" and history[0]["rank"] == 1
    unknown = client.get("/api/v1/instruments/000000/signals", headers=viewer).json()
    assert unknown["row"] is None and unknown["history"] == []
    assert client.get("/api/v1/instruments/..%2Fx/signals", headers=viewer).status_code == 404


def test_reports_announced_on_non_trading_days_are_loaded(tmp_path: Path) -> None:
    from datetime import date

    from quant_system.decision.reference import load_reference

    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE dividends (symbol VARCHAR, ex_date DATE)")
        con.execute("CREATE TABLE suspension_events (symbol VARCHAR, suspend_start DATE, suspend_end DATE, "
                    "expected_resume DATE)")
        con.execute("CREATE TABLE risk_warning_intervals (symbol VARCHAR, status VARCHAR, start_date DATE, "
                    "end_date DATE)")
        con.execute("CREATE TABLE fin_income (symbol VARCHAR, report_date DATE, notice_date DATE)")
        con.execute("INSERT INTO fin_income VALUES ('600000', DATE '2026-06-30', DATE '2026-08-29')")  # a Saturday
    reference = load_reference(path, date(2026, 8, 31))  # Monday's decision
    assert reference.reports_published(date(2026, 8, 28), date(2026, 8, 31)) == {"600000": [date(2026, 6, 30)]}
