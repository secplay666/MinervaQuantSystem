"""Position manager API (phase 1): library, labels, levels, board, signals, settings, stage views."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fastapi")

import duckdb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from quant_system.app.db import open_database  # noqa: E402
from quant_system.app.db.models import AuditLog, PmLevel, PmSettings  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from test_api import add_user, auth, login  # noqa: E402
from test_decision import make_root  # noqa: E402

DAYS = pd.bdate_range("2025-01-02", periods=320)
FACTOR = 2.0  # hfq = qfq x 2 for the stock


def stock_closes() -> np.ndarray:
    base = 10 + 0.2 * np.sin(np.arange(250) / 5)  # a long sideways base around 10
    rise = np.linspace(10.2, 18.0, 70)            # then a breakout towards 18
    return np.concatenate([base, rise])


def market_db(root: Path) -> None:
    path = root / "data" / "market.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    qfq = stock_closes()
    index = 4000 * 1.002 ** np.arange(len(DAYS))  # a steady advance
    stock = pd.DataFrame({"symbol": "600000", "trade_date": DAYS.date, "hfq_open": qfq * FACTOR * 0.995,
                          "hfq_close": qfq * FACTOR, "volume_shares": 1e6, "hfq_factor": FACTOR})
    indices = pd.concat([pd.DataFrame({"symbol": s, "name": n, "trade_date": DAYS.date, "open": index * 0.998,
                                       "close": index, "volume_shares": 1e9})
                         for s, n in (("sh000300", "沪深300"), ("sh000852", "中证1000"))])
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE security_master (symbol VARCHAR, name VARCHAR, exchange VARCHAR, board VARCHAR, "
                    "list_date DATE, delist_date DATE, status VARCHAR)")
        con.execute("INSERT INTO security_master VALUES ('600000', '浦发银行', 'SSE', 'SSE_MAIN', "
                    "DATE '1999-11-10', NULL, 'listed')")
        con.execute("CREATE TABLE daily_bars_adjusted AS SELECT * FROM stock")
        con.execute("CREATE TABLE index_bars AS SELECT * FROM indices")
        con.execute("CREATE TABLE trading_calendar AS SELECT trade_date FROM stock")


@pytest.fixture
def pm(tmp_path: Path):
    root = make_root(tmp_path / "root")
    market_db(root)
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    add_user(sessions, "bob", ["reviewer"])
    return client, sessions, auth(login(client, "alice")), auth(login(client, "bob"))


def add(client: TestClient, headers: dict, text: str) -> dict:
    response = client.post("/api/v1/pm/items", json={"text": text}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def item_id(client: TestClient, headers: dict, symbol: str) -> int:
    items = client.get("/api/v1/pm/items", headers=headers).json()["items"]
    return next(i["id"] for i in items if i["symbol"] == symbol)


def level(client: TestClient, headers: dict, item: int, **body) -> dict:
    response = client.post(f"/api/v1/pm/items/{item}/levels", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_library_parses_pasted_codes_and_belongs_to_one_user(pm) -> None:
    client, _, alice, bob = pm
    out = add(client, alice, "600000.SH\nsh000300, 999999 600000")
    assert out["added"] == ["600000", "sh000300"] and out["skipped"] == [] and len(out["errors"]) == 1
    assert add(client, alice, "sh600000")["skipped"] == ["600000"]
    rows = client.get("/api/v1/pm/items", headers=alice).json()["items"]
    assert {(r["symbol"], r["kind"], r["label"]) for r in rows} == {("600000", "stock", "undecided"),
                                                                   ("sh000300", "index", "undecided")}
    assert client.get("/api/v1/pm/items", headers=bob).json()["items"] == []
    mine = item_id(client, alice, "600000")
    assert client.get(f"/api/v1/pm/items/{mine}", headers=bob).status_code == 404
    patched = client.patch(f"/api/v1/pm/items/{mine}", json={"groups": ["银行", " "], "star": 2}, headers=alice)
    assert patched.status_code == 200
    assert client.get("/api/v1/pm/items", headers=alice).json()["groups"] == ["银行"]


def test_levels_are_kept_in_hfq_versioned_and_shown_in_qfq(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    level(client, alice, item, kind="neckline", price=11.0)
    detail = level(client, alice, item, kind="target", price=20.0)
    shown = {lv["kind"]: lv["price"] for lv in detail["levels"]}
    assert shown == pytest.approx({"neckline": 11.0, "target": 20.0})
    level(client, alice, item, kind="neckline", price=11.0)  # the same value: no new version
    detail = level(client, alice, item, kind="neckline", price=11.5)
    with sessions() as session:
        rows = session.scalars(select(PmLevel).where(PmLevel.kind == "neckline").order_by(PmLevel.id)).all()
        assert [(r.version, r.status, r.price) for r in rows] == [(1, "superseded", 22.0), (2, "active", 23.0)]
    ladder = detail["ladder"]
    assert [r["completion"] for r in ladder] == [0.7, 0.8, 0.9, 1.0]
    assert ladder[0]["price"] == pytest.approx(11.5 + 0.7 * 8.5) and ladder[0]["done_on"] is not None  # 18 > 17.45
    assert ladder[1]["done_on"] is None
    assert detail["item"]["neckline"] == pytest.approx(11.5) and detail["item"]["phase"] == "active"
    # Confirmed on the last session with the close already past 70%: the entry is sized by the ladder.
    assert [e["rule"] for e in reversed(detail["events"])] == ["activated", "entry"]
    assert detail["events"][0]["weight"] == 0.7 and detail["item"]["weight"] == 0.7
    assert detail["index_today"]["symbol"] == "sh000852" and detail["index_today"]["label"] == "right"
    bad = client.post(f"/api/v1/pm/items/{item}/levels", json={"kind": "top_neckline", "price": 17}, headers=alice)
    assert bad.status_code == 400  # observe or confirmed must be chosen


def test_labels_adopt_the_stage_view_unless_the_user_works_by_hand(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    detail = client.put(f"/api/v1/pm/items/{item}/label", json={"use_view": True}, headers=alice).json()
    assert detail["item"]["label"] == "right" and detail["item"]["label_source"] == "system"
    assert detail["label_history"][0]["reason"].startswith("右侧：200 日均线")
    assert detail["item"]["stage"]["label"] == "right"
    assert client.put("/api/v1/pm/settings", json={"label_mode": "manual"}, headers=alice).status_code == 200
    assert client.put(f"/api/v1/pm/items/{item}/label", json={"use_view": True}, headers=alice).status_code == 400
    detail = client.put(f"/api/v1/pm/items/{item}/label", json={"label": "top"}, headers=alice).json()
    assert detail["item"]["label"] == "top" and detail["item"]["stage"] is None
    assert [h["label"] for h in detail["label_history"]] == ["top", "right"]
    with sessions() as session:
        assert session.scalar(select(AuditLog).where(AuditLog.action == "pm.label").limit(1)) is not None


def test_the_board_stores_new_events_once_and_marks_them_read(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    level(client, alice, item, kind="neckline", price=11.0)
    level(client, alice, item, kind="target", price=20.0)
    with sessions() as session:  # as if the last evaluation was before the breakout
        session.get(PmSettings, session.scalar(select(PmSettings.user_id))).evaluated_through = date(2025, 1, 2)
        session.commit()
    board = client.get("/api/v1/pm/board", headers=alice).json()
    assert board["counts"]["right"] == 1 and board["latest"] == DAYS[-1].date().isoformat()
    assert {i["symbol"] for i in board["indices"]} == {"sh000300", "sh000852"}
    assert board["new_signals"] == 2 and board["unread"] == 2
    assert {s["rule"] for s in board["signals"]} == {"activated", "entry"}
    assert client.get("/api/v1/pm/board", headers=alice).json()["new_signals"] == 0  # stored once
    first = board["signals"][0]["id"]
    assert client.post("/api/v1/pm/signals/read", json={"ids": [first]}, headers=alice).json()["unread"] == \
        board["unread"] - 1
    assert client.post("/api/v1/pm/signals/read", json={}, headers=alice).json()["unread"] == 0
    assert client.get("/api/v1/pm/signals?unread=true", headers=alice).json()["signals"] == []


def test_settings_validate_and_presets_start_from_their_own_values(pm) -> None:
    client, _, alice, _ = pm
    settings = client.get("/api/v1/pm/settings", headers=alice).json()
    assert settings["stage_preset"] == "steady" and settings["stage_params"]["ma"] == 200
    assert {p["key"] for p in settings["presets"]} == {"steady", "classic", "short"}
    assert client.put("/api/v1/pm/settings", json={"stage_params": {"ma": 5}}, headers=alice).status_code == 400
    bad_ladder = {"rule_params": {"ladder": [[0.8, 0.5], [0.7, 0.7]]}}
    assert client.put("/api/v1/pm/settings", json=bad_ladder, headers=alice).status_code == 400
    out = client.put("/api/v1/pm/settings", json={"stage_params": {"confirm": 5}}, headers=alice).json()
    assert out["stage_params"]["confirm"] == 5
    out = client.put("/api/v1/pm/settings", json={"stage_preset": "classic"}, headers=alice).json()
    assert out["stage_params"]["ma"] == 150 and out["stage_overrides"] == {}
    out = client.put("/api/v1/pm/settings", json={"rule_params": {"trailing": 0.15}}, headers=alice).json()
    assert out["rule_params"]["trailing"] == 0.15 and out["rule_params"]["ladder"][0] == [0.7, 0.7]


def test_stage_bands_and_the_workstation_view(pm) -> None:
    client, _, alice, _ = pm
    out = client.get("/api/v1/pm/stage/600000", headers=alice).json()
    assert out["kind"] == "stock" and out["segments"][-1]["stage"] == "advance"
    assert out["segments"][0]["start"] == DAYS[0].date().isoformat()
    assert out["view"]["ma"] < 18  # on the chart's (qfq) scale, not hfq
    assert client.get("/api/v1/pm/stage/600000?preset=nope", headers=alice).status_code == 404
    chart = client.get("/api/v1/pm/chart/600000", headers=alice).json()
    assert chart["item_id"] is None and chart["stage"]["label"] == "right"
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    level(client, alice, item, kind="neckline", price=11.0)
    level(client, alice, item, kind="target", price=20.0)
    chart = client.get("/api/v1/pm/chart/600000", headers=alice).json()
    assert chart["item_id"] == item and {lv["kind"] for lv in chart["levels"]} == {"neckline", "target"}
    assert "activated" not in {e["rule"] for e in chart["events"]}
    assert client.get("/api/v1/pm/chart/999999", headers=alice).status_code == 404


def test_the_stage_backtest_runs_in_the_background_and_is_cached(pm, monkeypatch) -> None:
    import quant_system.app.position_jobs as jobs

    calls = []

    class Data:
        sessions = tuple(DAYS.date)

    def fake_backtest(data, index_close, params, start, end):
        calls.append((len(index_close), params.ma, start))
        return {"overall": {"stage2": {"return": np.float64(0.12), "drawdown": float("nan")}}}

    monkeypatch.setattr(jobs, "load_market_data", lambda source, path: Data())
    monkeypatch.setattr(jobs, "run_stage_backtest", fake_backtest)
    client, _, alice, bob = pm
    body = {"preset": "short", "start": "2025-06-02"}
    job = client.post("/api/v1/pm/stage-backtest", json=body, headers=alice)
    assert job.status_code == 202
    job_id = job.json()["id"]
    for _ in range(50):
        state = client.get(f"/api/v1/pm/jobs/{job_id}", headers=alice).json()
        if state["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    assert state["result"]["overall"]["stage2"] == {"return": 0.12, "drawdown": None}
    assert calls == [(len(DAYS), 60, date(2025, 6, 2))]
    again = client.post("/api/v1/pm/stage-backtest", json=body, headers=alice).json()
    assert again["status"] == "done" and len(calls) == 1  # cached
    assert client.get(f"/api/v1/pm/jobs/{job_id}", headers=bob).status_code == 404
    assert client.post("/api/v1/pm/stage-backtest", json={"params": {"ma": 1}}, headers=alice).status_code == 400


def test_the_daily_job_stores_signals_and_pushes_counts_without_securities(pm, tmp_path) -> None:
    from quant_system.app.db.models import Event, PmSignal
    from quant_system.app.market import MarketQueries
    from quant_system.app.position import run_daily

    client, sessions, alice, bob = pm
    add(client, alice, "600000")
    add(client, bob, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    level(client, alice, item, kind="neckline", price=11.0)
    level(client, alice, item, kind="target", price=20.0)
    client.put("/api/v1/pm/settings", json={"push_daily": True}, headers=alice)
    root = tmp_path / "root"
    market = MarketQueries(root / "data" / "market.duckdb", root / "configs" / "market_rules" / "cn_a_share.json")
    with sessions() as session:
        rows = run_daily(session, market)
        assert [(r["items"], r["new_signals"], r["error"]) for r in rows] == [(1, 2, None), (1, 0, None)]
        event = session.get(Event, rows[0]["event"])
        assert event.category == "position" and "alice" in event.title and "浦发" not in event.title
        assert "600000" not in event.title and rows[1]["event"] is None  # bob did not ask for the push
        assert [r["new_signals"] for r in run_daily(session, market)] == [0, 0]
        assert session.query(PmSignal).count() == 2 and session.query(Event).count() == 1
