from __future__ import annotations

from datetime import UTC, date, timedelta
from pathlib import Path

import pandas as pd

import quant_system.data_platform.pipeline as pipeline_module
from fakes import FakeProvider, at, make_config
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import read_canonical
from quant_system.data_platform.sw_index import SW_L1

EVENING = at(date(2026, 9, 30), 20, 0)


def _run(root: Path, provider: FakeProvider, monkeypatch, clock=EVENING) -> dict:
    manifests = root / "data" / "manifests"
    count = len(list(manifests.glob("*.json"))) if manifests.exists() else 0
    run_id = (clock.astimezone(UTC) + timedelta(seconds=count)).strftime("%Y%m%dT%H%M%SZ")
    monkeypatch.setattr(pipeline_module, "unique_run_id", lambda _root: run_id)
    return IngestionPipeline(root, make_config(), provider=provider, clock=lambda: clock).run()


def test_sw_indices_are_stored_in_yuan_and_shares_and_rebuild_the_same(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(sw_indices=True)
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    assert manifest["counters"]["sw_index_bars"] == {"updated": 31}
    electronics = read_canonical(tmp_path, "sw_index_bars", "symbol=801080")
    assert electronics["name"].unique().tolist() == ["电子"]
    assert electronics["trade_date"].max() == date(2026, 9, 30)
    first = electronics.iloc[0]
    assert first["turnover_cny"] == (10.0 + 80) * 1e8 and first["volume_shares"] == 150_000_000

    report = rebuild_canonical(tmp_path, make_config())
    staged = tmp_path / report["staging"]
    for code in SW_L1:
        pd.testing.assert_frame_equal(read_canonical(tmp_path, "sw_index_bars", f"symbol={code}"),
                                      read_canonical(staged, "sw_index_bars", f"symbol={code}"), check_dtype=False)


def test_a_failed_index_is_a_warning_and_the_others_are_stored(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(sw_indices=True, sw_failing={"801750"})
    manifest = _run(tmp_path, provider, monkeypatch)
    assert manifest["status"] == "complete"
    assert manifest["counters"]["sw_index_bars"] == {"updated": 30, "failed": 1}
    assert read_canonical(tmp_path, "sw_index_bars", "symbol=801750") is None
    issues = pd.read_parquet(tmp_path / manifest["sidecars"]["issues"])
    rows = issues[issues["dataset"] == "sw_index_bars"]
    assert rows["severity"].tolist() == ["warning"] and "1 个下载失败" in rows["message"].iloc[0]


def test_a_refresh_that_goes_back_in_time_is_kept_aside(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(sw_indices=True)
    _run(tmp_path, provider, monkeypatch)
    stored = read_canonical(tmp_path, "sw_index_bars", "symbol=801010")
    provider.today = date(2026, 9, 25)  # the service answers with an older history
    manifest = _run(tmp_path, provider, monkeypatch, clock=at(date(2026, 9, 30), 21, 0))
    assert manifest["counters"]["sw_index_bars"] == {"rejected": 31}
    pd.testing.assert_frame_equal(read_canonical(tmp_path, "sw_index_bars", "symbol=801010"), stored)
    report = rebuild_canonical(tmp_path, make_config())
    rebuilt = read_canonical(tmp_path / report["staging"], "sw_index_bars", "symbol=801010")
    assert rebuilt["trade_date"].max() == date(2026, 9, 30)  # the rejected response is not replayed


def test_a_provider_without_sw_indices_skips_them(tmp_path, monkeypatch) -> None:
    manifest = _run(tmp_path, FakeProvider(), monkeypatch)
    assert manifest["status"] == "complete" and "sw_index_bars" not in manifest["counters"]
