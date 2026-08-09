from __future__ import annotations

import json
from pathlib import Path

from quant_system.data_platform.config import DataPlatformConfig


def _write_config(path: Path, *, index_start_date: str | None) -> None:
    payload = {
        "provider": "akshare",
        "provider_version": "1.18.78",
        "market": "A_SHARE",
        "start_date": "20200101",
        "end_date": "20260809",
        "symbols": [],
        "indices": [],
    }
    if index_start_date is not None:
        payload["index_start_date"] = index_start_date
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_index_start_date_can_be_configured_independently(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, index_start_date="19900101")

    config = DataPlatformConfig.load(path)

    assert config.start_date == "20200101"
    assert config.index_start_date == "19900101"


def test_index_start_date_defaults_to_stock_start_date(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, index_start_date=None)

    config = DataPlatformConfig.load(path)

    assert config.index_start_date == "20200101"
