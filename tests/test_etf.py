from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import quant_system.data_platform.pipeline as pipeline_module
from fakes import OPEN_DATES, FakeProvider, at, make_config
from quant_system.data_platform.etf import (
    etf_fetch_log_row,
    merge_etf_shares,
    normalize_etf_sse,
    normalize_etf_szse,
    sse_etf_dates,
    szse_etf_months,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import read_canonical

SATURDAY_NIGHT = at(date(2026, 9, 26), 0, 30)  # latest final session 2026-09-24 (09-25 is a holiday)


def _run(root: Path, provider: FakeProvider, monkeypatch, clock=SATURDAY_NIGHT, **config) -> dict:
    # Run ids carry the fetch date that completeness is judged by; pin them to the fake clock.
    manifests = root / "data" / "manifests"
    count = len(list(manifests.glob("*.json"))) if manifests.exists() else 0
    run_id = (clock.astimezone(UTC) + timedelta(seconds=count)).strftime("%Y%m%dT%H%M%SZ")
    monkeypatch.setattr(pipeline_module, "unique_run_id", lambda _root: run_id)
    pipeline = IngestionPipeline(root, make_config(**config), provider=provider, clock=lambda: clock)
    return pipeline.run()


def _sse_calls(provider: FakeProvider) -> list[str]:
    return [subject for kind, subject in provider.calls if kind == "etf_sse"]


def test_sse_days_are_fetched_newest_first_a_few_per_run(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    manifest = _run(tmp_path, provider, monkeypatch, etf_sse_max_dates_per_run=5)
    assert manifest["status"] == "complete"
    assert _sse_calls(provider) == ["20260924", "20260923", "20260922", "20260921", "20260918"]
    assert manifest["counters"]["etf_shares"]["sse_backlog"] == 13
    shares = read_canonical(tmp_path, "etf_shares")
    latest = shares[(shares["symbol"] == "510300") & (shares["trade_date"] == date(2026, 9, 24))]
    assert latest["shares"].tolist() == [1e8 + 1e6 * OPEN_DATES.index(date(2026, 9, 24))]  # 10,000-share units
    szse = shares[shares["exchange"] == "SZSE"]
    assert szse["trade_date"].min() == date(2026, 9, 1) and len(szse) == 18
    provider.calls.clear()
    _run(tmp_path, provider, monkeypatch, etf_sse_max_dates_per_run=5)
    assert _sse_calls(provider) == ["20260917", "20260916", "20260915", "20260914", "20260911"]
    # The current month is fetched again every run; August was complete when first fetched.
    assert [s for kind, s in provider.calls if kind == "etf_szse"] == ["20260901"]


def test_an_unpublished_day_is_retried_and_a_failing_exchange_is_deferred(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(today=date(2026, 9, 24), etf_unpublished={date(2026, 9, 24)},
                            etf_failing={"sse:20260921", "sse:20260918"})
    manifest = _run(tmp_path, provider, monkeypatch)
    counters = manifest["counters"]["etf_shares"]
    assert manifest["status"] == "complete"  # a warning, not blocking
    assert counters["sse_empty"] == 1 and counters["sse_failed"] == 2 and counters["sse_deferred"] == 13
    assert _sse_calls(provider) == ["20260924", "20260923", "20260922", "20260921", "20260918"]
    issues = pd.read_parquet(tmp_path / manifest["sidecars"]["issues"])
    assert "fetch_windows" in set(issues.loc[issues["dataset"] == "etf_shares", "rule"])
    provider.etf_unpublished.clear()
    provider.etf_failing.clear()
    provider.calls.clear()
    _run(tmp_path, provider, monkeypatch)
    assert _sse_calls(provider)[0] == "20260924" and "20260923" not in _sse_calls(provider)
    assert len(_sse_calls(provider)) == 1 + 2 + 13
    shares = read_canonical(tmp_path, "etf_shares")
    assert shares.loc[shares["exchange"] == "SSE", "trade_date"].nunique() == 18


def test_rebuild_replays_etf_shares_and_their_log(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(today=date(2026, 9, 24), etf_unpublished={date(2026, 9, 24)})
    _run(tmp_path, provider, monkeypatch, etf_sse_max_dates_per_run=10)
    provider.etf_unpublished.clear()
    _run(tmp_path, provider, monkeypatch, etf_sse_max_dates_per_run=10)
    report = rebuild_canonical(tmp_path, make_config())
    staged = tmp_path / report["staging"]
    for dataset in ("etf_shares", "etf_shares_fetch_log"):
        pd.testing.assert_frame_equal(read_canonical(tmp_path, dataset), read_canonical(staged, dataset),
                                      check_dtype=False)


def test_sse_normalizer_converts_units_and_drops_bad_rows() -> None:
    raw = pd.DataFrame({
        "STAT_DATE": ["2026-09-28", "2026-09-28", "2026-09-25", "2026-09-28", "2026-09-28"],
        "ETF_TYPE": ["单市场ETF"] * 5,
        "SEC_CODE": ["510300", "510050", "510500", "512100", "510300"],
        "NUM": ["1", "2", "3", "4", "5"],
        "SEC_NAME": ["300ETF", "50ETF", "500ETF", "1000ETF", "300ETF"],
        "TOT_VOL": ["2417808.77", "736356.68", "1.00", "0", "2417809.77"],
    })
    frame, counts = normalize_etf_sse(raw, date(2026, 9, 28), "20260928T090000Z", "2026-09-28T09:00:00+00:00")
    assert counts == {"dropped_invalid": 1, "dropped_duplicate": 1, "dropped_other_date": 1}
    assert frame.set_index("symbol")["shares"].to_dict() == pytest.approx(
        {"510300": 24178097700.0, "510050": 7363566800.0})
    assert set(frame["exchange"]) == {"SSE"} and set(frame["trade_date"]) == {date(2026, 9, 28)}


def test_szse_normalizer_and_merge_keep_the_latest_fetch() -> None:
    raw = pd.DataFrame({"日期": [date(2026, 9, 28)], "基金代码": ["159919"], "基金简称": ["沪深300ETF嘉实"],
                        "基金份额": ["6,599,017,000"]})
    first, _ = normalize_etf_szse(raw, "20260928T090000Z", "2026-09-28T09:00:00+00:00")
    raw["基金份额"] = ["6,600,017,000"]
    second, _ = normalize_etf_szse(raw, "20260929T090000Z", "2026-09-29T09:00:00+00:00")
    merged = merge_etf_shares(merge_etf_shares(None, second), first)  # arrival order does not matter
    assert merged["shares"].tolist() == [6600017000.0]


def test_completeness_rules_for_days_and_months() -> None:
    assert etf_fetch_log_row("sse_20260924", 0, "20261010T090000Z") is None  # may still be published
    assert etf_fetch_log_row("sse_20260824", 0, "20261010T090000Z")["rows"] == 0  # no data that day
    log = pd.DataFrame([
        {"dataset": "etf_szse", "window": "szse_202607", "rows": 9, "run_id": "20260808T090000Z"},  # final
        {"dataset": "etf_szse", "window": "szse_202608", "rows": 9, "run_id": "20260903T090000Z"},  # too early
        {"dataset": "etf_sse", "window": "sse_20260924", "rows": 2, "run_id": "20260926T090000Z"},
    ])
    assert szse_etf_months(date(2026, 7, 1), date(2026, 9, 24), log) == [
        (date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 24))]
    days, left = sse_etf_dates(OPEN_DATES, date(2026, 9, 21), date(2026, 9, 24), log, 2)
    assert days == [date(2026, 9, 23), date(2026, 9, 22)] and left == 1


def _index_calls(provider: FakeProvider) -> list[str]:
    return sorted(subject for kind, subject in provider.calls if kind == "index" and subject[2:3] in "15")


def test_fund_lists_pick_the_broad_index_funds_whose_bars_are_kept(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    master = read_canonical(tmp_path, "etf_master").set_index("symbol")
    assert len(master) == 6
    assert master.loc["159919", ["index_code", "index_name"]].tolist() == ["399300", "沪深300"]
    assert master.loc["159920", "index_code"] == "HSI" and pd.isna(master.loc["159920", "index_name"])
    # Plain funds of configured indices only: not the enhanced 561990, the sector 512880 or the HSI 159920.
    assert _index_calls(provider) == ["sh510050", "sh510300", "sz159919"]
    bars = read_canonical(tmp_path, "etf_bars", "symbol=510300")
    assert set(bars["symbol"]) == {"510300"} and bars["trade_date"].max() == date(2026, 9, 24)

    # A fund that leaves the list keeps its record and its bars, but is no longer refreshed.
    provider.etf_sse_list = [row for row in provider.etf_sse_list if row["fundCode"] != "510050"]
    provider.calls.clear()
    _run(tmp_path, provider, monkeypatch)
    master = read_canonical(tmp_path, "etf_master").set_index("symbol")
    assert "510050" in master.index and master.loc["510050", "listed_run_id"] < master.loc["510300", "listed_run_id"]
    assert _index_calls(provider) == ["sh510300", "sz159919"]

    provider.etf_lists_failing = True
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    issues = pd.read_parquet(tmp_path / manifest["sidecars"]["issues"])
    assert "fetch" in set(issues.loc[issues["dataset"] == "etf_master", "rule"])
    assert len(read_canonical(tmp_path, "etf_master")) == 6

    report = rebuild_canonical(tmp_path, make_config())
    staged = tmp_path / report["staging"]
    pd.testing.assert_frame_equal(read_canonical(tmp_path, "etf_master"), read_canonical(staged, "etf_master"),
                                  check_dtype=False)
    for symbol in ("510300", "510050", "159919"):
        pd.testing.assert_frame_equal(read_canonical(tmp_path, "etf_bars", f"symbol={symbol}"),
                                      read_canonical(staged, "etf_bars", f"symbol={symbol}"), check_dtype=False)
