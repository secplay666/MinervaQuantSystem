"""回购增持 lists and tags, and 市场宽度 (app/company_actions.py, app/breadth.py) on a synthetic catalog."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from quant_system.app.breadth import BreadthMissing, MarketBreadth
from quant_system.app.company_actions import CompanyActions, CompanyActionsMissing

DAYS = pd.bdate_range("2025-01-02", periods=320)


def plan(plan_id: str, symbol: str, notice: str, latest: str, progress: str, kind: str, lower: float, done=None):
    return {"plan_id": plan_id, "symbol": symbol, "name": symbol, "notice_date": date.fromisoformat(notice),
            "latest_notice_date": date.fromisoformat(latest), "update_date": date.fromisoformat(latest),
            "progress": progress, "kind": kind, "purpose": "…", "amount_lower": lower, "amount_upper": lower * 2,
            "price_cap": 20.0, "start_date": None, "end_date": None, "done_amount": done, "done_shares": None,
            "done_avg_price": 8.0 if done else None, "finish_date": None}


def change(symbol: str, holder: str, direction: str, shares: float, pct: float, notice: str):
    return {"change_key": f"{symbol}{holder}{notice}", "symbol": symbol, "name": symbol, "holder": holder,
            "direction": direction, "change_shares": shares, "change_pct_total": pct, "hold_pct_after": 5.0,
            "avg_price": None, "channel": "二级市场", "start_date": None, "end_date": None,
            "notice_date": date.fromisoformat(notice)}


def catalog(path: Path, with_actions: bool = True) -> None:
    """Four stocks: A and B rise steadily, C and D fall; A, B in 银行, C, D in 电子; close 10 at the end."""
    rows = []
    for k, symbol in enumerate(["600001", "600002", "000003", "000004"]):
        step = 1.002 if k < 2 else 0.998
        prices = 10 * step ** (np.arange(len(DAYS)) - (len(DAYS) - 1))
        rows.append(pd.DataFrame({"symbol": symbol, "trade_date": DAYS.date, "close": prices, "hfq_close": prices}))
    bars = pd.concat(rows, ignore_index=True)  # noqa: F841 (read by DuckDB)
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE daily_bars_adjusted AS SELECT * FROM bars")
        con.execute("CREATE TABLE share_capital (symbol VARCHAR, change_date DATE, total_shares DOUBLE)")
        con.execute("INSERT INTO share_capital VALUES ('600001', DATE '2020-01-01', 1e9), ('000003', DATE '2020-01-01', 1e9)")
        con.execute("CREATE TABLE industry_sw (symbol VARCHAR, start_date DATE, end_date DATE, l1_name VARCHAR)")
        con.execute("INSERT INTO industry_sw VALUES ('600001', DATE '2020-01-01', NULL, '银行'), "
                    "('600002', DATE '2020-01-01', NULL, '银行'), ('000003', DATE '2020-01-01', NULL, '电子'), "
                    "('000004', DATE '2020-01-01', NULL, '电子')")
        index = pd.DataFrame({"symbol": "sh000300", "trade_date": DAYS.date, "close": 4000.0})  # noqa: F841
        con.execute("CREATE TABLE index_bars AS SELECT * FROM index")
        if with_actions:
            plans = pd.DataFrame([  # noqa: F841
                plan("P1", "600001", "2026-02-01", "2026-03-20", "004", "cancel", 2e8, 5e7),  # 2% of 10 billion
                plan("P2", "000003", "2025-06-01", "2025-06-10", "006", "incentive", 5e7),    # long ago
                plan("P3", "000003", "2026-03-01", "2026-03-15", "005", "other", 1e8),       # stopped
            ])
            changes = pd.DataFrame([  # noqa: F841
                change("000003", "某股东", "减持", 1e7, 1.0, "2026-03-18"),
                change("600001", "董事长", "增持", 1e6, 0.1, "2026-03-10"),
            ])
            con.execute("CREATE TABLE buybacks AS SELECT * FROM plans")
            con.execute("CREATE TABLE holder_changes AS SELECT * FROM changes")


def test_lists_filter_by_time_kind_and_size(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    catalog(path)
    actions = CompanyActions(path)
    result = actions.buybacks(days=90)
    assert result["as_of"] == date(2026, 3, 20) and [r["plan_id"] for r in result["rows"]] == ["P1", "P3"]
    p1 = result["rows"][0]
    assert p1["plan_pct_lower"] == pytest.approx(2.0) and p1["done_pct"] == pytest.approx(0.5)
    assert p1["progress_name"] == "实施中" and p1["kind_name"] == "注销" and p1["close_vs_avg"] == pytest.approx(10 / 8)
    assert [r["plan_id"] for r in actions.buybacks(days=90, kind="cancel", min_pct=1.0)["rows"]] == ["P1"]
    assert [r["plan_id"] for r in actions.buybacks(days=400)["rows"]] == ["P1", "P3", "P2"]
    holders = actions.holders(days=30)
    assert [r["direction"] for r in holders["rows"]] == ["减持", "增持"]
    assert holders["rows"][0]["amount"] == pytest.approx(1e7 * 10)  # no average price: the latest close
    industries = {r["industry"]: r for r in actions.industries(days=90)["rows"]}
    assert industries["银行"]["buyback_companies"] == 1 and industries["银行"]["increase_companies"] == 1
    assert industries["电子"]["buyback_companies"] == 0 and industries["电子"]["decrease_companies"] == 1


def test_tags_count_live_buybacks_and_holder_changes(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    catalog(path)
    actions = CompanyActions(path)
    assert actions.tags("600001")["buying"] and not actions.tags("600001")["reducing"]
    c = actions.tags("000003")
    assert not c["buying"] and c["reducing"]  # its buyback stopped; a holder sold
    assert actions.tags("600002") is None


def test_breadth_counts_the_market_and_its_industries(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    catalog(path, with_actions=False)
    overview = MarketBreadth(path).overview()
    latest = overview["latest"]
    assert latest["stocks"] == 4 and latest["above60"] == pytest.approx(0.5) and latest["above250"] == pytest.approx(0.5)
    assert latest["highs"] == 2 and latest["lows"] == 2 and latest["ups"] == 2 and latest["downs"] == 2
    assert overview["series"][0]["trade_date"] == str(DAYS[249].date())  # a year of bars before a stock counts
    by = {r["industry"]: r for r in overview["industries"]}
    assert by["银行"]["above60"] == 1.0 and by["电子"]["above60"] == 0.0 and by["银行"]["above60_change"] == 0.0


def test_missing_tables_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE index_bars (symbol VARCHAR, trade_date DATE, close DOUBLE)")
    with pytest.raises(CompanyActionsMissing):
        CompanyActions(path).buybacks()
    with pytest.raises(BreadthMissing):
        MarketBreadth(path).overview()


# -- API and the position manager ------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from quant_system.app.db import open_database  # noqa: E402
from quant_system.app.main import create_app  # noqa: E402
from quant_system.app.settings import AppSettings  # noqa: E402
from test_api import add_user, auth, login  # noqa: E402
from test_api_pm import add, market_db  # noqa: E402
from test_decision import make_root  # noqa: E402


def test_the_api_serves_the_lists_and_the_board_tags(tmp_path: Path) -> None:
    root = make_root(tmp_path / "root")
    market_db(root)  # the position manager's catalog: 600000 with bars, sh000300
    path = root / "data" / "market.duckdb"
    plans = pd.DataFrame([plan("P9", "600000", "2026-03-01", "2026-03-20", "004", "cancel", 1e8, 1e7)])  # noqa: F841
    changes = pd.DataFrame([change("600000", "某基金", "减持", 1e6, 0.2, "2026-03-19")])  # noqa: F841
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE buybacks AS SELECT * FROM plans")
        con.execute("CREATE TABLE holder_changes AS SELECT * FROM changes")
        con.execute("CREATE TABLE share_capital (symbol VARCHAR, change_date DATE, total_shares DOUBLE)")
        con.execute("INSERT INTO share_capital VALUES ('600000', DATE '2020-01-01', 2e10)")
        con.execute("ALTER TABLE daily_bars_adjusted ADD COLUMN close DOUBLE")
        con.execute("UPDATE daily_bars_adjusted SET close = hfq_close / hfq_factor")
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    alice = auth(login(client, "alice"))
    buybacks = client.get("/api/v1/company-actions/buybacks?days=90", headers=alice)
    assert buybacks.status_code == 200 and buybacks.json()["rows"][0]["plan_id"] == "P9"
    assert client.get("/api/v1/company-actions/holders?direction=减持", headers=alice).json()["total"] == 1
    assert client.get("/api/v1/company-actions/industries", headers=alice).status_code == 200
    assert client.get("/api/v1/company-actions/buybacks").status_code == 401
    breadth = client.get("/api/v1/breadth", headers=alice)
    assert breadth.status_code == 200 and breadth.json()["latest"]["stocks"] == 1
    add(client, alice, "600000")
    item = next(i for i in client.get("/api/v1/pm/items", headers=alice).json()["items"] if i["symbol"] == "600000")
    assert item["company"]["buying"] and item["company"]["reducing"]
