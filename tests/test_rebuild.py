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
    detail = report["diff"]["daily_bars_detail"]
    assert detail == {"only_live": 0, "only_rebuilt": 0, "close_changed": 0, "volume_changed": 0,
                      "symbols_volume_changed": 0}
    datasets = {key for key in report["diff"] if key != "daily_bars_detail"}
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
    assert report["diff"]["daily_bars_detail"]["symbols_volume_changed"] == 1
    rebuilt = pd.read_parquet(canonical_path(tmp_path, "daily_bars", "symbol=000001"))
    assert rebuilt["volume_shares"].iloc[0] == 100_000
    assert (tmp_path / report["archived_to"] / "daily_bars").exists()
    assert (tmp_path / "data" / "market.duckdb").exists()
