"""Stage-3 P3: financial statements (versioned ingestion) and point-in-time
fundamentals (TTM, single-quarter growth, availability rules)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from fakes import FakeProvider, at, make_config
from quant_system.data_platform.financials import (
    financial_windows,
    merge_financial_versions,
    normalize_financials,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import read_canonical
from quant_system.fundamentals.pit import OUTPUTS, derive_store, snapshot, ttm

SATURDAY_NIGHT = at(date(2026, 9, 26), 0, 30)


def test_normalize_keeps_a_share_quarter_ends_and_uses_bank_revenue() -> None:
    provider = FakeProvider()
    raw = pd.DataFrame(provider._financial_rows("income", "B"))
    raw.loc[len(raw)] = {**raw.iloc[0].to_dict(), "REPORT_DATE": "2025-05-31 00:00:00"}  # not a quarter end
    raw.loc[len(raw)] = {**raw.iloc[1].to_dict(), "NOTICE_DATE": None}
    frame, counts = normalize_financials(raw, "income", "B", "r1", "t1")
    assert set(frame["symbol"]) == {"000001"} and counts["not_a_share"] == 1
    assert counts["not_quarter_end"] == 1 and counts["no_notice_date"] == 1
    assert len(frame) == 5 and frame["notice_date"].notna().all()
    assert (frame["revenue"] == frame["operate_income"]).all() and frame["operate_cost"].isna().all()


def test_versions_are_added_only_when_curated_values_change() -> None:
    provider = FakeProvider()
    first, _ = normalize_financials(pd.DataFrame(provider._financial_rows("income", "G")), "income", "G", "r1", "t")
    merged, added = merge_financial_versions(None, first, "income")
    assert added == len(first) and (merged["version"] == 1).all()
    again, added = merge_financial_versions(merged, first.assign(run_id="r2"), "income")
    assert added == 0 and len(again) == len(merged)
    provider.fin_revision = True
    revised, _ = normalize_financials(pd.DataFrame(provider._financial_rows("income", "G")), "income", "G", "r3", "t")
    merged, added = merge_financial_versions(again, revised, "income")
    assert added == 1
    annual = merged[merged["report_date"] == date(2025, 12, 31)]
    assert annual["version"].tolist() == [1, 2] and annual["update_date"].tolist()[-1] == date(2026, 9, 20)


def test_windows_backfill_every_period_once_and_sweep_recent_weekly() -> None:
    first = financial_windows("2026-09-26", None)
    periods = [w for w in first if w[2] == "REPORT_DATE"]
    assert len(periods) == 4 * 42 and periods[0][3] == "2016-03-31"
    assert sum(w[2] == "UPDATE_DATE" for w in first) == 4
    log = pd.DataFrame([{"dataset": "fin_income", "window": w[0], "rows": 1, "run_id": "20260926T000000Z"}
                        for w in first])
    assert [w[2] for w in financial_windows("2026-09-27", log)] == ["UPDATE_DATE"] * 4
    week_later = financial_windows("2026-10-03", log)
    assert sum(w[2] == "REPORT_DATE" for w in week_later) == 4 * 6  # the six latest periods, per company type


def _pipeline(root, provider, clock=SATURDAY_NIGHT):
    return IngestionPipeline(root, make_config(), provider=provider, clock=lambda: clock)


def test_ingestion_versions_revisions_and_rebuilds_identically(tmp_path) -> None:
    provider = FakeProvider()
    assert _pipeline(tmp_path, provider).run()["status"] == "complete"
    income = read_canonical(tmp_path, "fin_income")
    assert set(income["symbol"]) == {"600001", "000001"} and (income["version"] == 1).all()
    provider.fin_revision = True
    manifest = _pipeline(tmp_path, provider, at(date(2026, 9, 28), 17, 0)).run()
    assert manifest["counters"]["fin_income"]["new_versions"] == 1
    income = read_canonical(tmp_path, "fin_income")
    assert income["version"].max() == 2
    report = rebuild_canonical(tmp_path, make_config())
    for dataset in ("fin_income", "fin_balance", "fin_cashflow", "fundamentals_fetch_log"):
        live = read_canonical(tmp_path, dataset)
        rebuilt = pd.read_parquet(tmp_path / report["staging"] / "data" / "canonical" / dataset / "data.parquet")
        pd.testing.assert_frame_equal(live, rebuilt, check_dtype=False)


# -- derivation ----------------------------------------------------------------------

def _state(income=None, balance=None, cashflow=None):
    return {"income": income or {}, "balance": balance or {}, "cashflow": cashflow or {}}


def _flows(values: dict[str, float], field="parent_net_profit"):
    return {date.fromisoformat(k): {field: v, "revenue": 10 * v, "operate_cost": 6 * v,
                                    "deducted_parent_net_profit": 0.9 * v} for k, v in values.items()}


def test_ttm_uses_ytd_plus_prior_annual_minus_prior_ytd() -> None:
    income = _flows({"2024-09-30": 30.0, "2024-12-31": 45.0, "2025-09-30": 36.0})
    assert ttm(income, date(2025, 9, 30), "parent_net_profit") == 36 + 45 - 30
    assert ttm(income, date(2024, 12, 31), "parent_net_profit") == 45
    assert math.isnan(ttm(income, date(2024, 9, 30), "parent_net_profit"))  # no 2023 data (e.g. new listing)


def test_snapshot_falls_back_to_the_latest_formable_period_and_computes_growth() -> None:
    income = _flows({"2024-03-31": 10.0, "2024-06-30": 22.0, "2024-12-31": 50.0, "2025-03-31": 12.0,
                     "2025-06-30": 26.0})
    # 2025Q2 TTM needs 2024Q2 and FY2024: available -> 26 + 50 - 22
    values, periods = snapshot(_state(income))
    out = dict(zip(OUTPUTS, values))
    assert out["ni_ttm"] == 54 and periods[0] == date(2025, 6, 30)
    assert out["ni_sq_yoy"] == pytest.approx((14 - 12) / 12)  # Q2 alone: 26-12 vs 22-10
    del income[date(2024, 6, 30)]
    values, periods = snapshot(_state(income))
    out = dict(zip(OUTPUTS, values))
    assert periods[0] == date(2025, 3, 31) and out["ni_ttm"] == 12 + 50 - 10  # fallback to Q1
    assert math.isnan(out["ni_sq_yoy"])  # latest period's quarter cannot be formed


def test_bank_gross_margin_missing_and_negative_equity_roe_missing() -> None:
    income = {date(2025, 12, 31): {"parent_net_profit": 5.0, "revenue": 50.0, "operate_cost": float("nan"),
                                   "deducted_parent_net_profit": 5.0}}
    balance = {date(2025, 12, 31): {"parent_equity": -3.0, "other_equity_instruments": 0.0,
                                    "total_assets": 100.0, "total_liabilities": 103.0}}
    out = dict(zip(OUTPUTS, snapshot(_state(income, balance))[0]))
    assert math.isnan(out["gross_profit_ttm"]) and out["avg_book"] == -3.0


def _tables(notice_shift_days: int = 0, revision_on: str | None = None):
    rows = {"income": [], "balance": [], "cashflow": []}
    notices = {"2020-12-31": "2021-03-30", "2021-03-31": "2021-04-28", "2021-06-30": "2021-08-20",
               "2021-09-30": "2021-10-28", "2021-12-31": "2022-03-29", "2020-09-30": "2020-10-29",
               "2020-06-30": "2020-08-25", "2020-03-31": "2020-04-28"}
    for period, notice in notices.items():
        notice_day = date.fromisoformat(notice) + timedelta(days=notice_shift_days)
        quarter = int(period[5:7]) // 3
        base = {"symbol": "600001", "report_date": date.fromisoformat(period), "notice_date": notice_day,
                "update_date": notice_day, "version": 1}
        rows["income"].append({**base, "revenue": 100.0 * quarter, "operate_cost": 60.0 * quarter,
                               "parent_net_profit": 10.0 * quarter * (1.2 if period >= "2021" else 1.0),
                               "deducted_parent_net_profit": 9.0 * quarter})
        rows["balance"].append({**base, "parent_equity": 500.0, "other_equity_instruments": 0.0,
                                "preferred_shares": np.nan, "perpetual_bonds": np.nan,
                                "total_assets": 1000.0, "total_liabilities": 500.0})
        rows["cashflow"].append({**base, "operating_cash_flow": 12.0 * quarter})
    if revision_on:
        rows["income"].append({"symbol": "600001", "report_date": date(2020, 12, 31), "notice_date": date(2021, 3, 30),
                               "update_date": date.fromisoformat(revision_on), "version": 2, "revenue": 400.0,
                               "operate_cost": 240.0, "parent_net_profit": 80.0, "deducted_parent_net_profit": 36.0})
    return {name: pd.DataFrame(value) for name, value in rows.items()}


SESSIONS = tuple(d.date() for d in pd.bdate_range("2020-01-02", "2022-12-30"))
SYMBOLS = np.array(["600001", "600002"], dtype=object)


def _first_use(store, field="ni_ttm"):
    values = store.asof(field, np.arange(len(SESSIONS)))[:, 0]
    return int(np.flatnonzero(np.isfinite(values))[0])


def test_notice_shift_delays_first_use_by_the_same_sessions() -> None:
    base = derive_store(_tables(0), SESSIONS, SYMBOLS)
    first = _first_use(base)
    # FY2020 (notice 2021-03-30, a Tuesday) is the first formable TTM: usable the next session.
    assert SESSIONS[first] == date(2021, 3, 31)
    shifted = derive_store(_tables(7), SESSIONS, SYMBOLS)  # one calendar week = five business days
    assert _first_use(shifted) == first + 5


def test_a_later_revision_only_changes_rows_after_it_is_available() -> None:
    plain = derive_store(_tables(), SESSIONS, SYMBOLS)
    revised = derive_store(_tables(revision_on="2021-06-15"), SESSIONS, SYMBOLS)
    rows = np.arange(len(SESSIONS))
    a, b = plain.asof("ni_ttm", rows)[:, 0], revised.asof("ni_ttm", rows)[:, 0]
    cut = SESSIONS.index(date(2021, 6, 15))
    np.testing.assert_array_equal(a[:cut + 1], b[:cut + 1])  # usable only after the revision date
    assert np.nanmax(np.abs(a[cut + 1:] - b[cut + 1:])) > 0
    # The sensitivity option moves a long-delayed first version to its update date.
    late = _tables()
    late["income"].loc[late["income"]["report_date"] == date(2020, 12, 31), "update_date"] = date(2021, 6, 15)
    lagged = derive_store(late, SESSIONS, SYMBOLS, restated_from_update=True)
    assert SESSIONS[_first_use(lagged)] > date(2021, 4, 28)


def test_stale_fundamentals_expire_a_year_after_the_period() -> None:
    tables = _tables()
    for name in tables:
        tables[name] = tables[name][tables[name]["report_date"] <= date(2021, 3, 31)]
    store = derive_store(tables, SESSIONS, SYMBOLS)
    rows = np.array([SESSIONS.index(date(2022, 3, 31)), SESSIONS.index(date(2022, 4, 1))])
    values = store.asof("ni_ttm", rows)[:, 0]
    assert np.isfinite(values[0]) and np.isnan(values[1])
    assert np.isnan(store.asof("ni_ttm", rows)[:, 1]).all()  # a symbol with no statements
