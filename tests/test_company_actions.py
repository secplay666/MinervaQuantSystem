"""Buybacks and holder increases/decreases (company_actions.py): normalization, merging, fetch windows, rebuild."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import quant_system.data_platform.pipeline as pipeline_module
from fakes import FakeProvider, at, make_config
from quant_system.data_platform.company_actions import (
    HOLDER_START,
    buyback_kind,
    buyback_windows,
    holder_windows,
    normalize_buybacks,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import read_canonical

EVENING = at(date(2026, 9, 30), 20, 0)


def _run(root: Path, provider: FakeProvider, monkeypatch, clock=EVENING) -> dict:
    manifests = root / "data" / "manifests"
    count = len(list(manifests.glob("*.json"))) if manifests.exists() else 0
    run_id = (clock.astimezone(UTC) + timedelta(seconds=count)).strftime("%Y%m%dT%H%M%SZ")
    monkeypatch.setattr(pipeline_module, "unique_run_id", lambda _root: run_id)
    return IngestionPipeline(root, make_config(), provider=provider, clock=lambda: clock).run()


def buyback(plan: str, symbol: str, notice: str, update: str, progress: str, purpose: str, done: float | None = None):
    return {"REPURCODE": plan, "DIM_SCODE": symbol, "SECUCODE": f"{symbol}.{'SH' if symbol[0] == '6' else 'SZ'}",
            "SECURITYSHORTNAME": "甲股份", "DIM_DATE": notice, "NOTICEDATE": update, "UPDATEDATE": update,
            "REPURPROGRESS": progress, "REPUROBJECTIVE": purpose, "REPURAMOUNTLOWER": 5e7, "REPURAMOUNTLIMIT": 1e8,
            "REPURPRICECAP": 20.0, "REPURAMOUNT": done, "REPURNUM": done / 15 if done else None,
            "ZJJG": 16.0 if done else 20.0, "FINISHDATE": update if progress == "006" else None}


def holder(symbol: str, name: str, direction: str, number: float, notice: str) -> dict:
    return {"SECURITY_CODE": symbol, "SECUCODE": f"{symbol}.SZ", "SECURITY_NAME_ABBR": "乙银行", "HOLDER_NAME": name,
            "DIRECTION": direction, "CHANGE_NUM": number, "AFTER_CHANGE_RATE": 0.5, "CHANGE_FREE_RATIO": 0.6,
            "HOLD_RATIO": 5.0, "TRADE_AVERAGE_PRICE": 9.9, "MARKET": "二级市场", "START_DATE": "2026-09-01",
            "END_DATE": "2026-09-20", "NOTICE_DATE": notice}


def test_kinds_follow_the_stated_purpose() -> None:
    purposes = pd.Series(["用于股权激励", "用于注销并减少注册资本", "维护公司价值", "员工持股计划或注销", None])
    assert buyback_kind(purposes).tolist() == ["incentive", "cancel", "other", "cancel", "other"]


def test_a_plan_without_a_latest_notice_dates_from_its_first() -> None:
    rows = [buyback("P1", "600001", "2026-09-01", "2026-09-20", "004", "注销"),
            {**buyback("P2", "600002", "2026-08-01", "2026-09-10", "001", "注销"), "NOTICEDATE": None}]
    frame = normalize_buybacks(pd.DataFrame(rows), "run", "2026-09-30T20:00:00")
    assert frame["latest_notice_date"].tolist() == [date(2026, 9, 20), date(2026, 8, 1)]


def test_the_average_price_is_amount_over_shares() -> None:
    rows = [buyback("P1", "600001", "2026-09-01", "2026-09-20", "004", "注销", done=3e7),
            buyback("P2", "600002", "2026-09-01", "2026-09-20", "001", "注销")]  # nothing bought yet
    frame = normalize_buybacks(pd.DataFrame(rows), "run", "2026-09-30T20:00:00")
    assert frame["done_avg_price"].iloc[0] == pytest.approx(15.0) and pd.isna(frame["done_avg_price"].iloc[1])


def test_windows_backfill_once_then_follow_what_is_stored() -> None:
    today = date(2026, 9, 30)
    assert buyback_windows(None, today) == [("all", "")]
    assert [name for name, _ in holder_windows(None, today)][0] == f"notice_{HOLDER_START}"
    assert len(holder_windows(None, today)) == today.year - HOLDER_START + 1
    stored = pd.DataFrame({"update_date": [date(2026, 9, 28)], "notice_date": [date(2026, 9, 28)]})
    assert buyback_windows(stored, today) == [("update_20260918", "(UPDATEDATE>='2026-09-18')")]
    assert holder_windows(stored, today) == [("notice_20260908", "(NOTICE_DATE>='2026-09-08')")]


def test_plans_keep_their_latest_state_and_changes_are_appended(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(company_actions=True)
    provider.buyback_rows = [buyback("A1", "600001", "2026-09-01", "2026-09-10", "004", "回购股份将予以注销", 3e7),
                             buyback("A2", "000001", "2026-09-05", "2026-09-05", "001", "用于股权激励")]
    provider.holder_rows = [holder("000001", "张三", "增持", 120.5, "2026-09-25"),
                            holder("000001", "某基金", "减持", 300.0, "2026-09-26")]
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    assert ("buybacks", "") in provider.calls
    assert len([c for c in provider.calls if c[0] == "holder_changes"]) == 2026 - HOLDER_START + 1
    plans = read_canonical(tmp_path, "buybacks").set_index("plan_id")
    assert plans.loc["A1", "kind"] == "cancel" and plans.loc["A2", "kind"] == "incentive"
    assert plans.loc["A1", "progress"] == "004" and plans.loc["A1", "done_amount"] == 3e7
    changes = read_canonical(tmp_path, "holder_changes")
    assert sorted(changes["direction"]) == ["减持", "增持"]
    assert changes.loc[changes["holder"] == "张三", "change_shares"].iloc[0] == 1_205_000  # 万股 -> shares

    # Next evening: plan A1 is done, a new change appears, an old one is delivered again.
    provider.calls.clear()
    provider.buyback_rows[0] = buyback("A1", "600001", "2026-09-01", "2026-09-30", "006", "回购股份将予以注销", 9e7)
    provider.holder_rows.append(holder("000001", "李四", "增持", 50.0, "2026-09-30"))
    manifest = _run(tmp_path, provider, monkeypatch, clock=at(date(2026, 10, 1), 20, 0))
    assert ("buybacks", "(UPDATEDATE>='2026-08-31')") in provider.calls  # 10 days before the newest stored update
    plans = read_canonical(tmp_path, "buybacks").set_index("plan_id")
    assert len(plans) == 2 and plans.loc["A1", "progress"] == "006" and plans.loc["A1", "done_amount"] == 9e7
    changes = read_canonical(tmp_path, "holder_changes")
    assert len(changes) == 3  # the re-delivered rows are not doubled

    report = rebuild_canonical(tmp_path, make_config())
    staged = tmp_path / report["staging"]
    for dataset in ("buybacks", "holder_changes"):
        pd.testing.assert_frame_equal(read_canonical(tmp_path, dataset), read_canonical(staged, dataset),
                                      check_dtype=False)


def test_a_failed_fetch_is_a_warning(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(company_actions=True, company_failing=True)
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    issues = pd.read_parquet(tmp_path / manifest["sidecars"]["issues"])
    rows = issues[issues["dataset"].isin(["buybacks", "holder_changes"])]
    assert set(rows["severity"]) == {"warning"} and len(rows) == 2
