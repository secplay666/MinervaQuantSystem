from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from fakes import FakeProvider, at, make_config
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import canonical_path, write_raw_frame


def _ingest(root: Path, provider: FakeProvider) -> dict:
    return IngestionPipeline(root, make_config(), provider=provider,
                             clock=lambda: at(date(2026, 9, 26), 0, 30)).run()


def test_rebuild_from_raw_reproduces_the_canonical_layer(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    provider.baidu_rows[date(2026, 9, 14)] = [
        {"股票代码": "600001", "股票简称": "甲股份", "交易所代码": "SH", "停牌时间": "2026-09-14",
         "复牌时间": "2026-09-14", "停牌事项说明": "盘中临时停牌"}
    ]
    _ingest(tmp_path, provider)
    _ingest(tmp_path, provider)  # a second, incremental run adds overlapping raw rows

    report = rebuild_canonical(tmp_path, make_config())

    assert report["status"] == "complete"
    for dataset in ("daily_bars", "index_bars", "adjustment_factors"):
        detail = report["diff"][f"{dataset}_detail"]
        assert set(detail.values()) == {0}, (dataset, detail)
    datasets = {key for key in report["diff"] if not key.endswith("_detail")}
    assert {"daily_bars", "adjustment_factors", "index_bars", "security_master", "suspension_events",
            "risk_warning_intervals", "trading_calendar", "market_snapshot"} <= datasets
    for dataset in datasets:
        assert report["diff"][dataset]["live"] == report["diff"][dataset]["rebuilt"], dataset


def test_rebuild_repairs_legacy_raw_units_and_applies(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    _ingest(tmp_path, provider)
    # A legacy raw file (no recorded source) from the old adapter, in lots.
    legacy = provider.fetch_daily_bars("000001", "20260901", "20260924").frame
    write_raw_frame(tmp_path, "akshare", "daily_bars", "20260801T000000Z", "000001", legacy)
    live = pd.read_parquet(canonical_path(tmp_path, "daily_bars", "symbol=000001"))
    live["volume_shares"] = live["volume_shares"] // 100  # what the old code stored
    live.to_parquet(canonical_path(tmp_path, "daily_bars", "symbol=000001"), index=False)

    report = rebuild_canonical(tmp_path, make_config(), apply=True)

    assert report["applied"]
    detail = report["diff"]["daily_bars_detail"]
    assert detail["symbols_changed"] == 1 and detail["volume_changed"] > 0
    assert detail["close_changed"] == detail["open_changed"] == detail["turnover_changed"] == 0
    assert report["data_version"] and report["catalog_status"] == "built"
    rebuilt = pd.read_parquet(canonical_path(tmp_path, "daily_bars", "symbol=000001"))
    assert rebuilt["volume_shares"].iloc[0] == 100_000
    assert (tmp_path / report["archived_to"] / "daily_bars").exists()
    assert (tmp_path / "data" / "market.duckdb").exists()


def test_swap_with_an_open_file_changes_nothing(tmp_path: Path) -> None:
    import sys

    import pytest

    from quant_system.data_platform.rebuild import CanonicalRebuilder

    if sys.platform != "win32":
        pytest.skip("open files only block directory renames on Windows")
    provider = FakeProvider(today=date(2026, 9, 24))
    _ingest(tmp_path, provider)
    rebuilder = CanonicalRebuilder(tmp_path, make_config())
    (rebuilder.staging / "data" / "canonical").mkdir(parents=True)
    held = canonical_path(tmp_path, "daily_bars", "symbol=600001")
    before = sorted(p.relative_to(tmp_path) for p in (tmp_path / "data" / "canonical").rglob("*"))
    with held.open("rb"):
        with pytest.raises(OSError):
            rebuilder._swap()
    after = sorted(p.relative_to(tmp_path) for p in (tmp_path / "data" / "canonical").rglob("*"))
    assert after == before
