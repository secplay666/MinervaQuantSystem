from __future__ import annotations

import pandas as pd

from quant_system.data_platform.quality import validate_bars


def test_quality_detects_invalid_ohlc() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": pd.Timestamp("2026-07-24").date(),
                "open": 10.0,
                "high": 9.0,
                "low": 8.0,
                "close": 10.5,
                "volume_shares": 100,
            }
        ]
    )
    issues = validate_bars(frame, "daily_bars", "000001")
    assert any(issue.rule == "valid_ohlc_relationship" for issue in issues)

