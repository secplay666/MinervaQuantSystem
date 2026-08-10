from __future__ import annotations

from quant_system.data_platform.providers.akshare_provider import AkShareProvider
import pandas as pd


def test_provider_adds_exchange_prefixes() -> None:
    assert AkShareProvider._with_market_prefix("600519") == "sh600519"
    assert AkShareProvider._with_market_prefix("000001") == "sz000001"
    assert AkShareProvider._with_market_prefix("920001") == "bj920001"
    assert AkShareProvider._with_market_prefix("sh000300") == "sh000300"


def test_provider_derives_adjustment_factors_from_prices() -> None:
    raw = pd.DataFrame(
        {"date": ["2026-01-01"], "close": [100.0]}
    )
    qfq = pd.DataFrame(
        {"date": ["2026-01-01"], "close": [80.0]}
    )
    hfq = pd.DataFrame(
        {"date": ["2026-01-01"], "close": [120.0]}
    )
    result = AkShareProvider._derive_adjustment_factors(raw, qfq, hfq)
    assert result.loc[0, "qfq_factor"] == 0.8
    assert result.loc[0, "hfq_factor"] == 1.2
