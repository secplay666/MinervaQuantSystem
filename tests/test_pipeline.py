from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from fakes import OPEN_DATES, Bar, FakeProvider, at, make_config
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.storage import canonical_path

SATURDAY_NIGHT = at(date(2026, 9, 26), 0, 30)


def run(root: Path, provider: FakeProvider, clock, mode: str = "incremental", **config) -> dict:
    pipeline = IngestionPipeline(root, make_config(**config), provider=provider, clock=lambda: clock)
    return pipeline.run(mode)


def bars(root: Path, symbol: str) -> pd.DataFrame:
    return pd.read_parquet(canonical_path(root, "daily_bars", f"symbol={symbol}"))


def issues(root: Path, manifest: dict) -> pd.DataFrame:
    return pd.read_parquet(root / manifest["sidecars"]["issues"])


def query(root: Path, sql: str) -> pd.DataFrame:
    with duckdb.connect(str(root / "data" / "market.duckdb"), read_only=True) as con:
        return con.execute(sql).fetchdf()


def blocking(root: Path, manifest: dict) -> list[str]:
    frame = issues(root, manifest)
    return frame.loc[frame["severity"] == "blocking", "message"].tolist()


def test_first_run_builds_survivorship_free_store(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)

    assert manifest["expected_latest_date"] == "2026-09-24"
    assert manifest["status"] == "complete", blocking(tmp_path, manifest)
    master = pd.read_parquet(canonical_path(tmp_path, "security_master")).set_index("symbol")
    assert "900901" not in master.index  # B share excluded
    assert master.loc["600002", "status"] == "delisted"
    assert master.loc["920002", "status"] == "delisted"  # manual BSE seed
    assert master.loc["300001", "board"] == "CHINEXT"
    # Delisted names keep their history up to the last trading day and are
    # fetched through the delisted path.
    assert ("daily_delisted", "920002") in provider.calls
    assert bars(tmp_path, "600002")["trade_date"].max() == date(2026, 9, 17)
    assert bars(tmp_path, "920002")["trade_date"].max() == date(2026, 9, 15)
    # sz000 volume arrives in lots from the adapter and is stored in shares.
    szse = bars(tmp_path, "000001")
    assert (szse["volume_scale"] == 100.0).all()
    assert szse["volume_shares"].iloc[0] == 100_000
    assert bars(tmp_path, "300001")["trade_date"].min() == date(2026, 9, 10)
    assert canonical_path(tmp_path, "market_snapshot", "snapshot_date=2026-09-24").exists()
    adjusted = query(tmp_path, "SELECT * FROM daily_bars_adjusted WHERE symbol = '600001' ORDER BY trade_date")
    assert (adjusted["hfq_close"] == adjusted["close"] * 1.5).all()
    assert (adjusted["qfq_close"].round(6) == adjusted["close"].round(6)).all()
    assert set(adjusted["factor_status"]) == {"ok"}
    runs = query(tmp_path, "SELECT run_id, status, data_version FROM ingestion_runs")
    assert runs["status"].tolist() == ["complete"]
    assert runs["data_version"].iloc[0] == manifest["data_version"]


def _extend_to(provider: FakeProvider, today: date) -> None:
    provider.today = today
    for symbol in ("600001", "000001", "300001", "920001", "688001"):
        for day in OPEN_DATES:
            if day <= today and day not in provider.bars[symbol]:
                index = OPEN_DATES.index(day)
                base = 10.0 + (int(symbol[-2:]) % 7) + index * 0.1
                provider.bars[symbol][day] = Bar(base - 0.05, base + 0.1, base - 0.15, base, 100_000)


def test_incremental_run_refreshes_factors_and_keeps_hfq_continuous(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    _extend_to(provider, date(2026, 9, 30))
    # 10-for-10 bonus issue: raw price halves on the ex-date, hfq doubles.
    for day, bar in provider.bars["600001"].items():
        if day >= date(2026, 9, 28):
            provider.bars["600001"][day] = Bar(bar.open / 2, bar.high / 2, bar.low / 2, bar.close / 2, bar.volume * 2)
    provider.factors["600001"].append(("2026-09-28", 3.0))
    provider.calls.clear()

    manifest = run(tmp_path, provider, at(date(2026, 9, 30), 17))

    assert manifest["status"] == "complete", blocking(tmp_path, manifest)
    assert ("factors", "600001") in provider.calls
    assert ("daily", "600002") not in provider.calls  # delisted history is final
    assert ("daily_delisted", "600002") not in provider.calls
    assert ("factors", "600002") not in provider.calls
    factors = pd.read_parquet(canonical_path(tmp_path, "adjustment_factors", "symbol=600001"))
    assert date(2026, 9, 28) in set(factors["effective_date"])
    assert set(factors["factor_as_of"]) == {date(2026, 9, 30)}
    adjusted = query(
        tmp_path,
        "SELECT trade_date, close, hfq_close, qfq_close FROM daily_bars_adjusted "
        "WHERE symbol = '600001' ORDER BY trade_date",
    )
    adjusted = adjusted.assign(trade_date=adjusted["trade_date"].dt.date).set_index("trade_date")
    before, after = adjusted.loc[date(2026, 9, 24)], adjusted.loc[date(2026, 9, 28)]
    assert after["close"] / before["close"] < 0.6
    assert abs(after["hfq_close"] / before["hfq_close"] - 1) < 0.05
    assert adjusted["qfq_close"].iloc[-1] == pytest.approx(adjusted["close"].iloc[-1])
    assert pd.Series(adjusted.index).is_monotonic_increasing


def test_run_before_the_close_excludes_the_current_session(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 29))
    manifest = run(tmp_path, provider, at(date(2026, 9, 29), 10, 30))

    assert manifest["expected_latest_date"] == "2026-09-28"
    assert bars(tmp_path, "600001")["trade_date"].max() == date(2026, 9, 28)
    assert not (tmp_path / "data" / "canonical" / "market_snapshot").exists()
    assert "end_of_day_only" in set(issues(tmp_path, manifest)["rule"])

    manifest = run(tmp_path, provider, at(date(2026, 9, 29), 17))
    assert manifest["expected_latest_date"] == "2026-09-29"
    assert bars(tmp_path, "600001")["trade_date"].max() == date(2026, 9, 29)


def test_overlap_window_replaces_revised_vendor_rows(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    revised = provider.bars["600001"][date(2026, 9, 23)]
    provider.bars["600001"][date(2026, 9, 23)] = replace(revised, close=revised.close + 0.01)

    run(tmp_path, provider, SATURDAY_NIGHT)

    stored = bars(tmp_path, "600001").set_index("trade_date")
    assert stored.loc[date(2026, 9, 23), "close"] == pytest.approx(revised.close + 0.01)


def test_empty_response_for_a_new_listing_blocks(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24), empty_daily={"300001"})
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)

    assert manifest["status"] == "partial"
    assert not canonical_path(tmp_path, "daily_bars", "symbol=300001").exists()
    assert any("300001" in message for message in blocking(tmp_path, manifest))


def test_empty_index_response_never_overwrites_history(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    path = canonical_path(tmp_path, "index_bars", "symbol=sh000001")
    before = pd.read_parquet(path)
    provider.empty_index = True

    manifest = run(tmp_path, provider, SATURDAY_NIGHT)

    assert manifest["status"] == "partial"
    pd.testing.assert_frame_equal(pd.read_parquet(path), before)


def test_blocking_bar_issues_quarantine_instead_of_overwriting(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    before = bars(tmp_path, "600001")
    bad = provider.bars["600001"][date(2026, 9, 24)]
    provider.bars["600001"][date(2026, 9, 24)] = replace(bad, high=bad.close - 1)

    manifest = run(tmp_path, provider, SATURDAY_NIGHT)

    assert manifest["status"] == "partial"
    pd.testing.assert_frame_equal(bars(tmp_path, "600001"), before)
    quarantined = tmp_path / "data" / "quarantine" / f"run_id={manifest['run_id']}" / "daily_bars" / "symbol=600001"
    assert (quarantined / "data.parquet").exists()


def test_missing_latest_bar_needs_a_suspension_record(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    del provider.bars["920001"][date(2026, 9, 24)]
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)
    coverage = issues(tmp_path, manifest)
    assert "latest_session_coverage" in set(coverage["rule"])

    provider.tfp_rows = [{"序号": 0, "代码": "920001", "名称": "丁精密", "停牌时间": "2026-09-24",
                          "停牌截止时间": "2026-09-24", "停牌期限": "停牌一天", "停牌原因": "刊登重要公告",
                          "所属市场": "北京证券交易所", "预计复牌时间": "2026-09-28"}]
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)
    assert manifest["status"] == "complete", blocking(tmp_path, manifest)
    gaps = pd.read_parquet(canonical_path(tmp_path, "bar_gaps")).set_index("symbol")
    assert gaps.loc["920001", "position"] == "tail"
    assert bool(gaps.loc["920001", "explained"])


def test_internal_gap_is_detected_and_explained_by_baidu(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    del provider.bars["600001"][date(2026, 9, 15)]
    run(tmp_path, provider, SATURDAY_NIGHT)
    gaps = pd.read_parquet(canonical_path(tmp_path, "bar_gaps")).set_index("symbol")
    assert gaps.loc["600001", "gap_start"] == date(2026, 9, 15)
    assert not bool(gaps.loc["600001", "explained"])

    provider.baidu_rows[date(2026, 9, 15)] = [
        {"股票代码": "600001", "股票简称": "甲股份", "交易所代码": "SH", "停牌时间": "2026-09-15",
         "复牌时间": "2026-09-16", "停牌事项说明": "重要公告"}
    ]
    # The fetch log marks 09-15 as done; a full rerun of the backfill picks it up.
    (tmp_path / "data" / "canonical" / "suspension_fetch_log" / "data.parquet").unlink()
    run(tmp_path, provider, SATURDAY_NIGHT)
    gaps = pd.read_parquet(canonical_path(tmp_path, "bar_gaps")).set_index("symbol")
    assert bool(gaps.loc["600001", "explained"])


def test_szse_name_history_becomes_dated_risk_intervals(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    intervals = pd.read_parquet(canonical_path(tmp_path, "risk_warning_intervals"))
    row = intervals[intervals["symbol"] == "000001"].iloc[0]
    assert row["status"] == "ST"
    assert row["end_date"] == date(2026, 9, 8)
    assert row["method"] == "szse_name_change"


def test_audit_runs_on_a_legacy_store_without_master_or_events(tmp_path: Path) -> None:
    from quant_system.data_platform.audit import run_audit
    from quant_system.data_platform.storage import write_canonical_frame

    write_canonical_frame(tmp_path, "trading_calendar", pd.DataFrame({"trade_date": OPEN_DATES[:3]}))
    frame = pd.DataFrame({"symbol": "600001", "trade_date": [OPEN_DATES[0], OPEN_DATES[2]], "open": 1.0,
                          "high": 1.0, "low": 1.0, "close": 1.0, "volume_shares": 100, "turnover_cny": 100.0})
    write_canonical_frame(tmp_path, "daily_bars", frame, partition="symbol=600001")
    result = run_audit(tmp_path, OPEN_DATES[2], OPEN_DATES[0], 0.98, "adhoc")
    assert result.gaps["gap_start"].tolist() == [OPEN_DATES[1]]


def test_limit_breach_rule_uses_code_board_and_half_up_rounding(tmp_path: Path) -> None:
    from quant_system.data_platform.audit import run_audit
    from quant_system.data_platform.storage import write_canonical_frame

    days = OPEN_DATES[:8]
    write_canonical_frame(tmp_path, "trading_calendar", pd.DataFrame({"trade_date": days}))
    closes = {
        "300001": [10.0] * 6 + [11.5, 11.5],   # +15%: legal on ChiNext, no master needed
        "600001": [3.95] * 6 + [4.345, 4.35],  # 3.95 * 1.1 = 4.345 rounds half-up to 4.35
        "600009": [10.0] * 6 + [11.2, 11.2],   # +12% on the main board: a real breach
    }
    for symbol, values in closes.items():
        frame = pd.DataFrame({"symbol": symbol, "trade_date": days, "open": values, "high": values,
                              "low": values, "close": values, "volume_shares": 100, "turnover_cny": 100.0})
        write_canonical_frame(tmp_path, "daily_bars", frame, partition=f"symbol={symbol}")
    factors = pd.DataFrame({"symbol": "600009", "effective_date": [date(1900, 1, 1)], "hfq_factor": [1.0],
                            "qfq_factor": [1.0], "factor_as_of": days[-1]})
    write_canonical_frame(tmp_path, "adjustment_factors", factors, partition="symbol=600009")
    result = run_audit(tmp_path, days[-1], days[0], 0.98, "adhoc")
    assert result.summary["unexplained_limit_breaches"] == 1


def test_recently_delisted_symbol_is_completed_before_it_freezes(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    _extend_to(provider, date(2026, 10, 12))
    # 600001 trades until 10-09 and is delisted on 10-12, after our last run.
    provider.bars["600001"] = {d: b for d, b in provider.bars["600001"].items() if d <= date(2026, 10, 9)}
    name, listed = provider.listed.pop("600001")
    provider.delisted_sse["600001"] = (name, listed, "2026-10-12")
    provider.calls.clear()

    run(tmp_path, provider, at(date(2026, 10, 12), 17))
    assert ("daily_delisted", "600001") in provider.calls
    assert bars(tmp_path, "600001")["trade_date"].max() == date(2026, 10, 9)

    provider.calls.clear()
    run(tmp_path, provider, at(date(2026, 10, 12), 18))
    assert ("daily_delisted", "600001") not in provider.calls  # now final
    assert ("factors", "600001") not in provider.calls


def test_security_master_never_drops_known_securities(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    provider.delisted_sse = {"600003": ("退市旧股", "2001-01-01", "2026-09-01")}  # 600002 vanished

    manifest = run(tmp_path, provider, SATURDAY_NIGHT)
    master = pd.read_parquet(canonical_path(tmp_path, "security_master")).set_index("symbol")
    assert master.loc["600002", "status"] == "delisted"
    assert "delisted_record_missing" in set(issues(tmp_path, manifest)["rule"])

    provider.delisted_sse = {}  # an empty delisting list is a broken response
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)
    assert manifest["status"] == "partial"
    master = pd.read_parquet(canonical_path(tmp_path, "security_master")).set_index("symbol")
    assert {"600002", "600003"} <= set(master.index)


def test_empty_baidu_response_is_retried_next_run(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    event = {"股票代码": "600001", "股票简称": "甲股份", "交易所代码": "SH", "停牌时间": "2026-09-15",
             "复牌时间": "2026-09-16", "停牌事项说明": "重要公告"}
    original = provider.fetch_suspension_events
    provider.fetch_suspension_events = lambda date_text: pd.DataFrame()  # throttled
    manifest = run(tmp_path, provider, SATURDAY_NIGHT)
    assert "baidu_empty_response" in set(issues(tmp_path, manifest)["rule"])

    provider.fetch_suspension_events = original
    provider.baidu_rows[date(2026, 9, 15)] = [event]
    run(tmp_path, provider, SATURDAY_NIGHT)
    events = pd.read_parquet(canonical_path(tmp_path, "suspension_events"))
    assert "600001" in set(events["symbol"])


def test_full_reload_cannot_shrink_existing_history(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, SATURDAY_NIGHT)
    before = bars(tmp_path, "600001")
    provider.bars["600001"] = {d: b for d, b in provider.bars["600001"].items() if d >= date(2026, 9, 20)}

    manifest = run(tmp_path, provider, SATURDAY_NIGHT, mode="full")

    assert manifest["status"] == "partial"
    assert "history_not_shrunk" in set(issues(tmp_path, manifest)["rule"])
    pd.testing.assert_frame_equal(bars(tmp_path, "600001"), before)
