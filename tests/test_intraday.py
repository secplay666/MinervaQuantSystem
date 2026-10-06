"""Intraday collection: bars and trades of the latest session, the session check, the rebuild."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from fakes import FakeProvider, at, make_config
from quant_system.data_platform.intraday import (
    check_trades, final_day_of_run, normalize_bars, normalize_trades, symbol_of,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import canonical_path

TODAY = date(2026, 9, 24)


def ingest(root: Path, provider: FakeProvider) -> dict:
    return IngestionPipeline(root, make_config(), provider=provider, clock=lambda: at(date(2026, 9, 26), 0, 30)).run()


def test_codes_bars_and_trades_are_normalized() -> None:
    assert symbol_of("sh510300") == "510300" and symbol_of("sh000300") == "sh000300" and symbol_of("IF0") == "IF0"
    raw = pd.DataFrame({"day": ["2026-09-23 14:59:00", "2026-09-23 15:00:00", "2026-09-24 09:31:00",
                                "2026-09-25 15:00:00"],
                        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 10, "amount": 10.0})
    bars = normalize_bars(raw, "sh510300", date(2026, 9, 24), "r", "t", "s")
    assert list(bars["time"]) == ["14:59", "15:00", "09:31"]  # 09-25 was not over when fetched
    assert final_day_of_run("20260924T080500Z") == date(2026, 9, 24)  # 16:05 in Shanghai
    assert final_day_of_run("20260924T020000Z") == date(2026, 9, 23)  # 10:00, the session still open
    trades = normalize_trades(pd.DataFrame({"成交时间": ["14:59:57"], "成交价格": [4.4], "价格变动": [0.0],
                                            "成交量": [12.0], "成交金额": [5280.0], "性质": ["卖盘"]}),
                              "sh510300", TODAY, "r", "t")
    assert trades.iloc[0][["symbol", "volume", "side"]].tolist() == ["510300", 1200.0, "S"]


def test_trades_must_match_the_session_bars() -> None:
    bars = pd.DataFrame({"time": ["14:59", "15:00"], "close": [4.40, 4.41], "amount": [1e6, 1e6]})
    good = pd.DataFrame({"price": [4.40, 4.41], "amount": [1.0e6, 0.99e6]})
    assert check_trades(good, bars) is None
    assert "不符" in check_trades(good.assign(amount=[0.5e6, 0.5e6]), bars)
    assert "不符" in check_trades(good.assign(price=[4.40, 4.39]), bars)
    assert "无法确认" in check_trades(good, bars.iloc[0:0])


def test_the_evening_run_stores_bars_of_every_kept_session_and_checked_trades(tmp_path: Path) -> None:
    provider = FakeProvider(today=TODAY)
    report = ingest(tmp_path, provider)
    days = sorted(p.name for p in (tmp_path / "data" / "canonical" / "intraday_bars").iterdir())
    assert days == ["trade_date=2026-09-22", "trade_date=2026-09-23", "trade_date=2026-09-24"]
    bars = pd.read_parquet(canonical_path(tmp_path, "intraday_bars", "trade_date=2026-09-24"))
    assert set(bars["symbol"]) == {"510300", "510310", "510330", "159919", "515330", "510360", "515380", "510350",
                                   "sh000300", "IF0"}
    assert len(bars[bars["symbol"] == "510300"]) == 240
    trades = pd.read_parquet(canonical_path(tmp_path, "intraday_trades", "trade_date=2026-09-24"))
    assert len(trades["symbol"].unique()) == 8 and set(trades["side"]) == {"B", "S", "N"}  # funds only
    assert not (tmp_path / "data" / "canonical" / "intraday_trades" / "trade_date=2026-09-23").exists()
    assert not any(i["dataset"] == "intraday_trades" for i in report.get("quality_issues", []))


def test_trades_of_another_session_are_kept_aside_and_the_rebuild_agrees(tmp_path: Path) -> None:
    provider = FakeProvider(today=TODAY)
    ingest(tmp_path, provider)
    provider.intraday_trades_lag = 1  # the source still serves the session before
    provider.today = date(2026, 9, 25)
    IngestionPipeline(tmp_path, make_config(), provider=provider, clock=lambda: at(date(2026, 9, 26), 0, 30)).run()
    assert not (tmp_path / "data" / "canonical" / "intraday_trades" / "trade_date=2026-09-25").exists()
    rejected = list((tmp_path / "data" / "raw").glob("*/intraday_trades_rejected/*/*.parquet"))
    assert len(rejected) == 8
    report = rebuild_canonical(tmp_path, make_config())
    for dataset in ("intraday_bars", "intraday_trades"):
        assert report["diff"][dataset]["live"] == report["diff"][dataset]["rebuilt"], dataset
