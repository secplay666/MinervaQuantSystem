from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import quant_system.data_platform.pipeline as pipeline_module
from fakes import FakeProvider, at, make_config
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import canonical_path, read_canonical

SATURDAY_NIGHT = at(date(2026, 9, 26), 0, 30)  # latest final session 2026-09-24


def run(root: Path, provider: FakeProvider, start: str, steps: list[str] | None = None) -> dict:
    pipeline = IngestionPipeline(root, make_config(start_date=start), provider=provider,
                                 clock=lambda: SATURDAY_NIGHT)
    return pipeline.run(steps=steps)


def first_bar(root: Path, symbol: str) -> date:
    return pd.read_parquet(canonical_path(root, "daily_bars", f"symbol={symbol}"))["trade_date"].min()


def daily_calls(provider: FakeProvider) -> list[str]:
    return [symbol for kind, symbol in provider.calls if kind.startswith("daily")]


def test_moving_start_earlier_leaves_the_daily_run_alone_and_backfill_fills_heads(tmp_path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    assert run(tmp_path, provider, "20260910")["status"] == "complete"
    assert first_bar(tmp_path, "600001") == date(2026, 9, 10)

    # The daily run with the earlier start only extends the tail.
    manifest = run(tmp_path, provider, "20260901")
    assert manifest["status"] == "complete"
    assert first_bar(tmp_path, "600001") == date(2026, 9, 10)

    provider.calls.clear()
    manifest = run(tmp_path, provider, "20260901", ["bars_history"])
    counters = manifest["counters"]["bars_history"]
    assert manifest["status"] == "complete"
    assert counters["done"] == 6 and counters["complete"] == 1  # 300001 listed on 09-10
    for symbol in ("600001", "000001", "688001", "920001", "600002", "920002"):
        assert first_bar(tmp_path, symbol) == date(2026, 9, 1), symbol
    bars = pd.read_parquet(canonical_path(tmp_path, "daily_bars", "symbol=600001"))
    joined = bars.set_index("trade_date")
    # The first previously stored bar now has a previous close.
    assert joined.loc[date(2026, 9, 10), "change_cny"] == pytest.approx(
        joined.loc[date(2026, 9, 10), "close"] - joined.loc[date(2026, 9, 9), "close"])
    assert len(bars) == bars["trade_date"].nunique()
    log = read_canonical(tmp_path, "daily_bars_history_log")
    assert set(log["status"]) == {"done"}
    assert log.set_index("symbol").loc["600001", "head_end"] == date(2026, 9, 9)

    provider.calls.clear()
    run(tmp_path, provider, "20260901", ["bars_history"])
    assert daily_calls(provider) == []  # nothing left to backfill

    report = rebuild_canonical(tmp_path, make_config(start_date="20260901"))
    assert set(report["diff"]["daily_bars_detail"].values()) == {0}
    assert report["diff"]["daily_bars"]["live"] == report["diff"]["daily_bars"]["rebuilt"]
    assert report["diff"]["daily_bars_history_log"]["live"] == report["diff"]["daily_bars_history_log"]["rebuilt"]


def test_a_head_failing_validation_is_kept_aside_as_a_warning(tmp_path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    run(tmp_path, provider, "20260910")
    original = provider.fetch_daily_bars

    def lots_instead_of_shares(symbol, start_date, end_date, delisted=False):
        result = original(symbol, start_date, end_date, delisted)
        if symbol == "600001":
            result.frame["volume"] = result.frame["volume"] / 100
        return result

    provider.fetch_daily_bars = lots_instead_of_shares  # type: ignore[method-assign]
    manifest = run(tmp_path, provider, "20260901", ["bars_history"])
    assert manifest["status"] == "complete"
    assert manifest["counters"]["bars_history"]["quarantined"] == 1
    assert first_bar(tmp_path, "600001") == date(2026, 9, 10)  # stored bars untouched
    raw = tmp_path / "data" / "raw" / "akshare"
    assert list((raw / "daily_bars_history_rejected").glob("run_id=*/600001.parquet"))
    assert not [p for p in (raw / "daily_bars").glob(f"run_id={manifest['run_id']}/600001.parquet")]
    issues = pd.read_parquet(tmp_path / manifest["sidecars"]["issues"])
    rejected = issues[issues["dataset"] == "bars_history"]
    assert set(rejected["severity"]) == {"warning"} and rejected["message"].str.startswith("历史回补未采用").all()
    log = read_canonical(tmp_path, "daily_bars_history_log").set_index("symbol")
    assert log.loc["600001", "status"] == "quarantined" and "volume" in log.loc["600001", "detail"]

    provider.calls.clear()
    run(tmp_path, provider, "20260901", ["bars_history"])
    assert "600001" not in daily_calls(provider)  # a rejected head waits for a manual decision
    report = rebuild_canonical(tmp_path, make_config(start_date="20260901"))
    assert report["diff"]["daily_bars"]["live"] == report["diff"]["daily_bars"]["rebuilt"]


def test_long_delisted_symbols_without_bars_are_left_to_the_backfill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "HISTORY_ONLY_DELISTED_DAYS", 3)  # 600002 left on 09-18
    provider = FakeProvider(today=date(2026, 9, 24))
    manifest = run(tmp_path, provider, "20260901")
    assert manifest["counters"]["daily_bars"]["history_pending"] == 2  # 600002 and 920002
    assert "600002" not in daily_calls(provider)
    assert not canonical_path(tmp_path, "daily_bars", "symbol=600002").exists()

    provider.calls.clear()
    manifest = run(tmp_path, provider, "20260901", ["bars_history"])
    assert ("daily_delisted", "600002") in provider.calls
    assert manifest["counters"]["bars_history"]["done"] == 2
    bars = pd.read_parquet(canonical_path(tmp_path, "daily_bars", "symbol=600002"))
    assert bars["trade_date"].min() == date(2026, 9, 1) and bars["trade_date"].max() == date(2026, 9, 17)

    provider.calls.clear()
    manifest = run(tmp_path, provider, "20260901")
    assert manifest["counters"]["daily_bars"]["frozen_delisted"] >= 1
    assert "600002" not in daily_calls(provider)
    assert ("factors", "600002") in provider.calls  # factors follow once bars exist


def test_an_empty_history_is_logged_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "HISTORY_ONLY_DELISTED_DAYS", 3)
    provider = FakeProvider(today=date(2026, 9, 24), empty_daily={"600002"})
    run(tmp_path, provider, "20260901")
    manifest = run(tmp_path, provider, "20260901", ["bars_history"])
    assert manifest["status"] == "complete"
    assert manifest["counters"]["bars_history"]["no_data"] == 1
    provider.calls.clear()
    run(tmp_path, provider, "20260901", ["bars_history"])
    assert daily_calls(provider) == []


def test_unknown_steps_are_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="bars_history"):
        run(tmp_path, FakeProvider(), "20260901", ["bars_historie"])
