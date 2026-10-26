"""Shareholder counts (data_platform/shareholders.py): normalization, windows, ingestion and rebuild; the
point-in-time store and the candidate factor (fundamentals/holders.py, features/holders.py); the 大涨后股东户数大增
rule (position/holders.py) and its reminder, settings and the chart's endpoint."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import quant_system.data_platform.pipeline as pipeline_module
from fakes import FakeProvider, at, make_config
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.shareholders import COUNT_START, count_windows, normalize_shareholder_counts
from quant_system.data_platform.storage import read_canonical
from quant_system.features.registry import all_factors
from quant_system.fundamentals.holders import MAX_AGE_DAYS, derive_holder_store, previous_quarter_end
from quant_system.position.holders import quarter_changes, surges


def count(symbol: str, end: str, notice: str, a=None, total=None, top10=None, b_mark="0", approx=None) -> dict:
    suffix = "SH" if symbol[0] == "6" else "NQ" if symbol[0] == "8" else "SZ"
    return {"SECURITY_CODE": symbol, "SECUCODE": f"{symbol}.{suffix}", "END_DATE": f"{end} 00:00:00",
            "NOTICE_DATE": f"{notice} 00:00:00" if notice else None, "HOLDER_A_NUM": a, "HOLDER_TOTAL_NUM": total,
            "HOLD_RATIO_TOTAL": top10, "FREEHOLD_RATIO_TOTAL": top10, "AVG_FREE_SHARES": 1000.0, "B_MARK": b_mark,
            "IS_APPROXIMATION": approx}


# -- normalization and windows ------------------------------------------------------------------------

def test_normalization_counts_a_share_holders_and_keeps_dated_rows() -> None:
    raw = pd.DataFrame([
        count("600001", "2026-06-30", "2026-08-20", a=50_000, total=50_000, top10=60.5),
        count("000002", "2026-07-10", "2026-07-15", total=30_000, approx="1"),     # no A count, no B shares
        count("000003", "2026-06-30", "2026-08-25", total=90_000, top10=40.0, b_mark="1"),  # B shares: no A count
        count("830001", "2026-06-30", "2026-08-20", a=1_000),                        # NEEQ
        count("600004", "2026-06-30", "2026-06-01", a=1_000),                        # announced before its date
        count("600005", "2026-06-30", None, a=1_000),                                # no notice date
    ])
    frame = normalize_shareholder_counts(raw, "run", "2026-10-26T20:00:00").set_index("symbol")
    assert sorted(frame.index) == ["000002", "000003", "600001"]
    assert frame.loc["600001", "holders"] == 50_000 and frame.loc["600001", "top10_pct"] == 60.5
    assert frame.loc["000002", "holders"] == 30_000 and bool(frame.loc["000002", "approximate"])
    assert pd.isna(frame.loc["000003", "holders"]) and frame.loc["000003", "top10_pct"] == 40.0
    assert frame.loc["600001", "end_date"] == date(2026, 6, 30) and frame.loc["600001", "notice_date"] == date(2026, 8, 20)


def test_windows_backfill_by_year_then_follow_what_is_stored() -> None:
    today = date(2026, 10, 26)
    windows = count_windows(None, today)
    assert windows[0] == (f"notice_{COUNT_START}", f"(NOTICE_DATE>='{COUNT_START}-01-01')(NOTICE_DATE<'{COUNT_START + 1}-01-01')")
    assert len(windows) == today.year - COUNT_START + 1
    stored = pd.DataFrame({"notice_date": [date(2026, 10, 24)]})
    assert count_windows(stored, today) == [("notice_20260924", "(NOTICE_DATE>='2026-09-24')")]


def _run(root: Path, provider: FakeProvider, monkeypatch, clock) -> dict:
    manifests = root / "data" / "manifests"
    n = len(list(manifests.glob("*.json"))) if manifests.exists() else 0
    run_id = (clock.astimezone(UTC) + timedelta(seconds=n)).strftime("%Y%m%dT%H%M%SZ")
    monkeypatch.setattr(pipeline_module, "unique_run_id", lambda _root: run_id)
    return IngestionPipeline(root, make_config(), provider=provider, clock=lambda: clock).run()


def test_ingestion_replaces_a_count_delivered_again_and_the_rebuild_matches(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(company_actions=True)
    provider.count_rows = [count("600001", "2026-03-31", "2026-04-20", a=50_000, top10=60.0),
                           count("600001", "2026-06-30", "2026-08-20", a=52_000, top10=61.0)]
    manifest = _run(tmp_path, provider, monkeypatch, at(date(2026, 9, 30), 20, 0))
    assert manifest["status"] == "complete"
    assert len([c for c in provider.calls if c[0] == "shareholder_counts"]) == 2026 - COUNT_START + 1
    assert len(read_canonical(tmp_path, "shareholder_counts")) == 2

    provider.calls.clear()  # next evening: the vendor corrects the June count
    provider.count_rows[1] = count("600001", "2026-06-30", "2026-08-20", a=51_000, top10=61.0)
    _run(tmp_path, provider, monkeypatch, at(date(2026, 10, 1), 20, 0))
    assert [c for c in provider.calls if c[0] == "shareholder_counts"] == [
        ("shareholder_counts", "(NOTICE_DATE>='2026-07-21')")]
    stored = read_canonical(tmp_path, "shareholder_counts").set_index("end_date")
    assert len(stored) == 2 and stored.loc[date(2026, 6, 30), "holders"] == 51_000

    report = rebuild_canonical(tmp_path, make_config())
    pd.testing.assert_frame_equal(read_canonical(tmp_path, "shareholder_counts"),
                                  read_canonical(tmp_path / report["staging"], "shareholder_counts"), check_dtype=False)


# -- point-in-time store and the candidate factor -----------------------------------------------------

SESSIONS = tuple(d.date() for d in pd.bdate_range("2026-01-02", "2027-06-30"))


def _row(day: date) -> int:
    return SESSIONS.index(day)


def test_the_change_is_quarter_on_quarter_and_known_after_the_notice_day() -> None:
    frame = pd.DataFrame([
        {"symbol": "600001", "end_date": date(2026, 3, 31), "notice_date": date(2026, 4, 20), "holders": 100.0},
        {"symbol": "600001", "end_date": date(2026, 7, 10), "notice_date": date(2026, 7, 15), "holders": 95.0},
        {"symbol": "600001", "end_date": date(2026, 6, 30), "notice_date": date(2026, 8, 20), "holders": 80.0},
        {"symbol": "000002", "end_date": date(2026, 6, 30), "notice_date": date(2026, 8, 20), "holders": 50.0},
    ])
    store = derive_holder_store(frame, SESSIONS, np.array(["600001", "000002"]))
    rows = np.array([_row(date(2026, 4, 21)), _row(date(2026, 8, 20)), _row(date(2026, 8, 21)),
                     _row(date(2027, 2, 25)), _row(date(2027, 2, 26))])
    values = store.asof(rows)
    assert np.isnan(values[0, 0])                       # the quarter before Q1 is unknown
    assert np.isnan(values[1, 0])                       # not on the notice day itself
    assert values[2, 0] == pytest.approx(-0.2)          # Q2 on Q1 (the July count is not a quarter end)
    assert values[3, 0] == pytest.approx(-0.2)          # 240 days after the quarter end ...
    assert (date(2027, 2, 26) - date(2026, 6, 30)).days > MAX_AGE_DAYS and np.isnan(values[4, 0])  # ... then stale
    assert np.isnan(values[:, 1]).all()                 # one count only
    assert previous_quarter_end(date(2026, 3, 31)) == date(2025, 12, 31)


def test_the_factor_is_a_candidate_left_out_of_all() -> None:
    from quant_system.strategy.multifactor import MultiFactorParams

    assert "holders_qoq" not in {s.id for s in all_factors()}
    spec = next(s for s in all_factors(candidates=True) if s.id == "holders_qoq")
    assert spec.candidate and spec.direction == -1 and spec.family == "holders"
    assert "holders_qoq" not in MultiFactorParams.from_payload({}).factors
    assert "holders_qoq" in MultiFactorParams.from_payload({"factors": ["holders_qoq", "rev_1m"]}).factors


# -- the 大涨后股东户数大增 rule --------------------------------------------------------------------------

def test_a_surge_needs_both_the_run_up_and_the_rise() -> None:
    dates = [d.date() for d in pd.bdate_range("2026-01-02", periods=200)]
    close = np.concatenate([np.full(60, 10.0), np.linspace(10, 18, 140)])  # +80% into the end
    counts = pd.DataFrame({"end_date": [date(2026, 3, 31), date(2026, 6, 30), date(2026, 8, 31)],
                           "notice_date": [date(2026, 4, 25), dates[-3], dates[-2]],
                           "holders": [100.0, 130.0, 200.0]})
    assert [(e, round(c, 2)) for e, _, c in quarter_changes(counts)] == [(date(2026, 6, 30), 0.3)]
    found = surges(dates, close, counts, run=0.5, rise=0.2)
    assert len(found) == 1 and found[0].day == dates[-3] and found[0].end_date == date(2026, 6, 30)
    assert found[0].run == pytest.approx(close[-3] / close[-123] - 1)
    assert surges(dates, close, counts, run=0.5, rise=0.35) == []    # the count rose 30% only
    assert surges(dates, close, counts, run=1.0, rise=0.2) == []     # the stock rose less than 100%


# -- the reminder, its settings and the chart's endpoint ---------------------------------------------

pytest.importorskip("fastapi")

import duckdb  # noqa: E402
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
def pm_holders(tmp_path: Path):
    root = make_root(tmp_path / "root")
    market_db(root)  # 600000: a long base, then +80% over the last 70 sessions
    counts = pd.DataFrame({  # noqa: F841 (read by DuckDB)
        "symbol": "600000", "end_date": [date(2025, 9, 30), date(2025, 12, 31), date(2026, 1, 20)],
        "notice_date": [date(2025, 10, 25), DAYS[-5].date(), DAYS[-4].date()], "holders": [40_000.0, 52_000.0, 53_000.0],
        "top10_pct": [55.0, 50.0, None], "top10_free_pct": [45.0, 41.0, None], "avg_free_shares": [1.0, 1.0, 1.0],
        "approximate": [False, False, True]})
    with duckdb.connect(str(root / "data" / "market.duckdb")) as con:
        con.execute("CREATE TABLE shareholder_counts AS SELECT * FROM counts")
    settings = AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48)
    client = TestClient(create_app(settings))
    _, sessions = open_database(settings.db_path)
    add_user(sessions, "alice", ["viewer"])
    return client, sessions, auth(login(client, "alice"))


def test_the_position_manager_reminds_after_a_run_up_and_a_surge(pm_holders) -> None:
    client, sessions, alice = pm_holders
    add(client, alice, "600000")
    client.get("/api/v1/pm/settings", headers=alice)  # creates the settings row
    with sessions() as session:
        session.scalar(select(PmSettings)).evaluated_through = DAYS[-10].date()
        session.commit()
    assert client.get("/api/v1/pm/board", headers=alice).status_code == 200
    with sessions() as session:
        signals = session.scalars(select(PmSignal).where(PmSignal.rule == "holders_surge")).all()
        assert len(signals) == 1 and signals[0].priority == 4 and signals[0].trade_date == DAYS[-5].date()
        assert "+30%" in signals[0].message
    row = next(i for i in client.get("/api/v1/pm/items", headers=alice).json()["items"] if i["symbol"] == "600000")
    assert row["holders"]["end_date"] == "2025-12-31" and row["holders"]["change"] == pytest.approx(0.3)


def test_the_thresholds_are_settings(pm_holders) -> None:
    client, sessions, alice = pm_holders
    settings = client.get("/api/v1/pm/settings", headers=alice).json()
    assert (settings["holder_run"], settings["holder_rise"]) == (0.5, 0.2)
    changed = client.put("/api/v1/pm/settings", json={"holder_run": 1.0, "holder_rise": 0.3}, headers=alice)
    assert changed.status_code == 200 and changed.json()["holder_run"] == 1.0
    assert client.put("/api/v1/pm/settings", json={"holder_rise": 0.01}, headers=alice).status_code == 400
    add(client, alice, "600000")  # +80% is below the 100% now asked for: no tag
    row = next(i for i in client.get("/api/v1/pm/items", headers=alice).json()["items"] if i["symbol"] == "600000")
    assert row["holders"] is None


def test_the_chart_endpoint_serves_counts_with_their_change(pm_holders) -> None:
    client, _, alice = pm_holders
    rows = client.get("/api/v1/instruments/600000/holders", headers=alice).json()
    assert [r["end_date"] for r in rows] == ["2025-09-30", "2025-12-31", "2026-01-20"]
    assert rows[0]["change"] is None and rows[1]["change"] == pytest.approx(0.3)
    assert rows[2]["top10_pct"] is None and rows[2]["approximate"] is True
    assert client.get("/api/v1/instruments/abc/holders", headers=alice).status_code == 404
