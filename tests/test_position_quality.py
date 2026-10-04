"""Quality grade (phase 2): dimension scores, the grade, and the point-in-time inputs."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quant_system.app.quality import quality_values
from quant_system.position.quality import grade, growth_score, margin_trend_score, peg_score, roe_score


def test_dimension_scores_follow_the_design_table() -> None:
    assert peg_score(10, 0.25) == 100 and peg_score(20, 0.25) == 80 and peg_score(60, 0.25) == 40 and peg_score(70, 0.25) == 15
    assert peg_score(-5, 0.3) == 15 and peg_score(20, -0.1) == 15 and peg_score(None, 0.2) is None
    assert growth_score(0.35) == 100 and growth_score(0.02) == 45 and growth_score(-0.5) == 10
    assert roe_score(0.18) == 85 and roe_score(-0.01) == 10
    assert margin_trend_score(2.0) == 80 and margin_trend_score(-5) == 20


def test_the_grade_needs_three_dimensions_and_shows_pe_beside_peg() -> None:
    full = grade({"pe": 10, "profit_growth": 0.25, "revenue_growth": 0.176, "roe": 0.182, "margin_change": 2.0}, "预增")
    assert full["grade"] == "A" and full["score"] == pytest.approx((100 + 80 + 80 + 85 + 80 + 100) / 6, abs=0.05)
    assert full["dims"]["peg"]["value"] == pytest.approx(0.4) and full["dims"]["peg"]["pe"] == 10
    sparse = grade({"roe": 0.3, "pe": float("nan")}, "续亏")
    assert sparse["grade"] is None and sparse["dims"]["forecast"]["score"] == 10
    assert grade({"pe": 80, "profit_growth": -0.3, "revenue_growth": -0.3, "roe": -0.05}, None)["grade"] == "D"


def catalog(path: Path) -> None:
    days = [d.date() for d in pd.bdate_range("2025-01-02", "2026-09-24")]
    income = pd.DataFrame([
        {"symbol": "600000", "report_date": date(2024, 12, 31), "company_type": "G", "notice_date": date(2025, 3, 20),
         "update_date": None, "version": 1, "revenue": 8.5e9, "operate_cost": 5.27e9, "parent_net_profit": 8e8,
         "deducted_parent_net_profit": 7.5e8},
        {"symbol": "600000", "report_date": date(2025, 12, 31), "company_type": "G", "notice_date": date(2026, 3, 20),
         "update_date": None, "version": 1, "revenue": 1e10, "operate_cost": 6e9, "parent_net_profit": 1e9,
         "deducted_parent_net_profit": 9.5e8}])
    balance = pd.DataFrame([
        {"symbol": "600000", "report_date": d, "company_type": "G", "notice_date": n, "update_date": None, "version": 1,
         "parent_equity": e, "other_equity_instruments": 0.0, "preferred_shares": None, "perpetual_bonds": None,
         "total_assets": 2e10, "total_liabilities": 1e10}
        for d, n, e in ((date(2024, 12, 31), date(2025, 3, 20), 5e9), (date(2025, 12, 31), date(2026, 3, 20), 6e9))])
    forecast = pd.DataFrame([{"symbol": "600000", "report_date": date(2026, 6, 30), "finance_code": "004",
                              "notice_date": date(2026, 7, 10), "predict_type": "预增"}])
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE trading_calendar AS SELECT d AS trade_date, true AS is_open FROM "
                    "(SELECT unnest(?) AS d)", [days])
        con.execute("CREATE TABLE daily_bars AS SELECT '600000' AS symbol, DATE '2026-09-24' AS trade_date, "
                    "10.0 AS close")
        con.execute("CREATE TABLE share_capital AS SELECT '600000' AS symbol, DATE '2025-06-30' AS change_date, "
                    "DATE '2025-07-01' AS notice_date, 1e9 AS total_shares")
        for name, frame in (("fin_income", income), ("fin_balance", balance), ("earnings_forecast", forecast)):
            con.register("frame", frame)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
            con.unregister("frame")


def test_inputs_are_known_now_and_a_year_ago(tmp_path: Path) -> None:
    catalog(tmp_path / "market.duckdb")
    with duckdb.connect(str(tmp_path / "market.duckdb"), read_only=True) as con:
        as_of, values = quality_values(con, ["600000", "600036"])
    assert as_of == date(2026, 9, 24)
    v = values["600000"]
    assert v["pe"] == pytest.approx(10.0) and v["profit_growth"] == pytest.approx(0.25)
    assert v["revenue_growth"] == pytest.approx(1e10 / 8.5e9 - 1) and v["roe"] == pytest.approx(1e9 / 5.5e9)
    assert v["margin_change"] == pytest.approx((0.4 - 3.23e9 / 8.5e9) * 100) and v["forecast"] == "预增"
    assert grade(v, v["forecast"])["grade"] == "A"
    assert all(pd.isna(values["600036"][k]) for k in ("pe", "roe")) and values["600036"]["forecast"] is None
    assert date(2026, 9, 24) - timedelta(days=365) > date(2025, 3, 20)  # a year ago the 2024 report was known
