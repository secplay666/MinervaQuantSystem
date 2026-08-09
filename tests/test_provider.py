from __future__ import annotations

from quant_system.data_platform.providers.akshare_provider import AkShareProvider


def test_provider_adds_exchange_prefixes() -> None:
    assert AkShareProvider._with_market_prefix("600519") == "sh600519"
    assert AkShareProvider._with_market_prefix("000001") == "sz000001"
    assert AkShareProvider._with_market_prefix("920001") == "bj920001"
    assert AkShareProvider._with_market_prefix("sh000300") == "sh000300"
