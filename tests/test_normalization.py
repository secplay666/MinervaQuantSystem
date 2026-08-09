from __future__ import annotations

import pandas as pd

from quant_system.data_platform.normalization import (
    infer_exchange,
    normalize_daily_bars,
)


def test_infer_exchange() -> None:
    assert infer_exchange("600519") == "SSE"
    assert infer_exchange("300750") == "SZSE"
    assert infer_exchange("920002") == "BSE"


def test_normalize_daily_bars_converts_lots_to_shares() -> None:
    raw = pd.DataFrame(
        [
            {
                "日期": "2026-07-24",
                "股票代码": "600519",
                "开盘": 1400.0,
                "收盘": 1410.0,
                "最高": 1420.0,
                "最低": 1390.0,
                "成交量": 123,
                "成交额": 17_000_000.0,
                "振幅": 2.1,
                "涨跌幅": 0.7,
                "涨跌额": 10.0,
                "换手率": 0.2,
            }
        ]
    )
    result = normalize_daily_bars(raw, "600519", "run", "2026-07-25T00:00:00Z")
    assert result.loc[0, "volume_shares"] == 12_300
    assert result.loc[0, "price_adjustment"] == "NONE"

