"""Earnings forecasts (业绩预告): normalization, merge, fetch windows, ingestion and rebuild."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from fakes import FakeProvider, at, make_config
from quant_system.data_platform.forecasts import (
    forecast_windows, latest_profit_forecast, merge_forecasts, normalize_forecasts,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import canonical_path


def test_normalize_keeps_a_shares_with_a_notice_and_merge_keeps_every_announcement() -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    raw = pd.DataFrame(provider.forecast_rows + [{"SECURITY_CODE": "600036", "REPORT_DATE": "2026-06-30",
                                                   "PREDICT_FINANCE_CODE": "004"}])  # no notice date
    frame = normalize_forecasts(raw, "20260924T100000Z", "2026-09-24T10:00:00+00:00")
    assert list(frame["symbol"]) == ["600000", "600000"]  # the B share and the undated row are dropped
    assert list(frame["predict_type"]) == ["预增", "略增"] and frame["amount_upper"].iloc[0] == 1.2e9
    again = merge_forecasts(frame, frame.iloc[[1]].assign(content="内容更新"))
    assert len(again) == 2 and again["content"].iloc[1] == "内容更新"
    latest = latest_profit_forecast(again, "600000", date(2026, 9, 20))
    assert latest["type"] == "略增" and str(latest["notice_date"]) == "2026-09-14"
    assert latest_profit_forecast(again, "600000", date(2026, 7, 11))["type"] == "预增"
    assert latest_profit_forecast(again, "600000", date(2027, 6, 1)) is None  # older than six months


def test_windows_fetch_each_period_once_sweep_the_recent_ones_weekly_and_always_the_latest_notices() -> None:
    windows = forecast_windows(date(2026, 9, 24), None)
    names = [w[0] for w in windows]
    assert names[0] == "period_2020-03-31" and "period_2026-12-31" in names and names[-1].startswith("update_")
    log = pd.DataFrame([{"dataset": "earnings_forecast", "window": n, "rows": 1, "run_id": "20260923T100000Z"}
                        for n in names])
    later = [w[0] for w in forecast_windows(date(2026, 9, 24), log)]
    assert later == [names[-1]]  # one day later only the notice window
    week = [w[0] for w in forecast_windows(date(2026, 10, 1), log)]
    assert week[:4] == ["period_2026-03-31", "period_2026-06-30", "period_2026-09-30", "period_2026-12-31"]


def test_ingestion_stores_forecasts_and_the_rebuild_reproduces_them(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 24))
    IngestionPipeline(tmp_path, make_config(), provider=provider, clock=lambda: at(date(2026, 9, 26), 0, 30)).run()
    stored = pd.read_parquet(canonical_path(tmp_path, "earnings_forecast"))
    assert list(stored["predict_type"]) == ["预增", "略增"]
    assert any(name == "forecasts" for name, _ in provider.calls)
    report = rebuild_canonical(tmp_path, make_config())
    assert report["diff"]["earnings_forecast"]["live"] == report["diff"]["earnings_forecast"]["rebuilt"]
    assert report["diff"]["fundamentals_fetch_log"]["live"] == report["diff"]["fundamentals_fetch_log"]["rebuilt"]
