"""Exchange-traded options: contract terms, normalization, what to fetch, the morning step and the rebuild."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from fakes import OPEN_DATES, FakeProvider, at, make_config
from quant_system.data_platform.options import (
    expiry_day,
    log_row,
    normalize_cffex_daily,
    normalize_sse_minutes,
    normalize_sse_risk,
    sessions_todo,
    sse_contracts,
    sse_daily_todo,
)
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import canonical_path, read_canonical

OPTION_DATASETS = ("option_contracts", "option_daily_bars", "option_risk", "option_minute_bars", "option_minute_totals",
                   "option_fetch_log")


def morning(root: Path, provider: FakeProvider, day: date, **config: object) -> dict:
    """The catch-up at 09:05 on ``day``: the latest final session is the one before."""
    pipeline = IngestionPipeline(root, make_config(**config), provider=provider, clock=lambda: at(day, 9, 5))
    return pipeline.run(steps={"options"})


def issues(root: Path, report: dict) -> pd.DataFrame:
    return pd.read_parquet(root / report["sidecars"]["issues"])


def calls(provider: FakeProvider, kind: str) -> list[str]:
    return [key for name, key in provider.calls if name == f"option_{kind}"]


def test_expiry_follows_the_exchange_rules_and_moves_past_closed_days() -> None:
    assert expiry_day("2026-09", "SSE", OPEN_DATES) == date(2026, 9, 23)  # fourth Wednesday
    assert expiry_day("2026-10", "CFFEX", OPEN_DATES) == date(2026, 10, 16)  # third Friday
    closed = [d for d in OPEN_DATES if d != date(2026, 9, 23)]
    assert expiry_day("2026-09", "SSE", closed) == date(2026, 9, 24)
    assert expiry_day("2030-01", "SSE", OPEN_DATES) == date(2030, 1, 23)  # beyond the calendar: the rule's day


def test_sse_terms_come_from_the_code_and_an_adjusted_strike_from_the_name() -> None:
    raw = FakeProvider(today=date(2026, 9, 22), options=True).fetch_option_risk_sse("20260922")
    terms = sse_contracts(raw, OPEN_DATES, "r", "t").set_index("contract")
    call = terms.loc["10009001"]
    assert (call["underlying"], call["kind"], call["expiry_month"], call["strike"], call["multiplier"]) == \
        ("510050", "call", "2026-09", 2.8, 10_000)
    assert call["expiry_date"] == date(2026, 9, 23) and not call["adjusted"]
    adjusted = terms.loc["10009004"]
    assert adjusted["kind"] == "put" and adjusted["adjusted"] and adjusted["strike"] == 2.755
    assert np.isnan(adjusted["multiplier"])  # changed by the adjustment; not in the code
    risk = normalize_sse_risk(raw, "r", "t").set_index("contract")
    assert np.isnan(risk.loc["10009002", "iv"]) and risk.loc["10009001", "iv"] == 0.18


def test_cffex_rows_are_the_options_only_with_amounts_in_yuan() -> None:
    raw = FakeProvider(today=date(2026, 9, 18), options=True).fetch_cffex_daily("20260918")
    contracts, bars = normalize_cffex_daily(raw, date(2026, 9, 18), OPEN_DATES, "r", "t")
    assert sorted(contracts["contract"]) == ["IO2609-C-4000", "IO2609-P-4000", "MO2610-C-7000"]  # padding stripped
    io = contracts.set_index("contract").loc["IO2609-P-4000"]
    assert (io["underlying"], io["kind"], io["strike"], io["expiry_date"], io["multiplier"]) == \
        ("IO", "put", 4000, date(2026, 9, 18), 100)
    bar = bars.set_index("contract").loc["MO2610-C-7000"]
    assert bar["amount"] == 105_000 and bar["settle"] == 54.0 and bar["open_interest"] == 300
    assert bar["exchange_delta"] == 0.45 and bar["trade_date"] == date(2026, 9, 18)


def test_minutes_are_kept_for_their_session_only() -> None:
    provider = FakeProvider(today=date(2026, 9, 23), options=True)
    raw = provider.fetch_option_minute_sse("10009003")
    frame = normalize_sse_minutes(raw, "10009003", "510050", date(2026, 9, 23), "r", "t")
    assert len(frame) == 240 and np.isnan(frame.iloc[0]["price"]) and frame.iloc[1]["price"] == 0.1
    assert normalize_sse_minutes(raw, "10009003", "510050", date(2026, 9, 22), "r", "t").empty


def test_empty_recent_sessions_stay_open_and_contracts_are_due_until_fetched_after_their_last_session() -> None:
    assert log_row("sse_risk", "2026-09-23", 0, "r", date(2026, 9, 23), date(2026, 9, 23)) is None
    assert log_row("sse_risk", "2026-09-01", 0, "r", date(2026, 9, 23), date(2026, 9, 1))["rows"] == 0
    log = pd.DataFrame([{"dataset": "sse_risk", "window": "2026-09-01", "rows": 4, "through": date(2026, 9, 2),
                         "run_id": "r"}])
    assert sessions_todo(OPEN_DATES[:3], OPEN_DATES[0], log, "sse_risk") == OPEN_DATES[1:3]
    contracts = sse_contracts(FakeProvider(today=date(2026, 9, 22), options=True).fetch_option_risk_sse("20260922"),
                              OPEN_DATES, "r", "t")
    fetched = pd.DataFrame([{"dataset": "sse_daily", "window": c, "rows": 10, "through": date(2026, 9, 23),
                             "run_id": "r"} for c in ("10009001", "10009003")])
    # Expired 09-23 and fetched through it: done; listed and fetched through 09-23 only: due on 09-24.
    assert sse_daily_todo(contracts, fetched, date(2026, 9, 24)) == ["10009002", "10009003", "10009004"]
    assert sse_daily_todo(contracts, fetched, date(2026, 9, 23)) == ["10009002", "10009004"]


def test_the_morning_step_backfills_then_fetches_only_what_is_new(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 23), options=True)
    report = morning(tmp_path, provider, date(2026, 9, 24))
    sessions = [d for d in OPEN_DATES if d <= date(2026, 9, 23)]
    assert len(calls(provider, "risk")) == len(sessions) == len(calls(provider, "cffex")) == 17
    contracts = read_canonical(tmp_path, "option_contracts")
    assert len(contracts) == 7 and set(contracts["exchange"]) == {"SSE", "CFFEX"}
    risk = read_canonical(tmp_path, "option_risk", "underlying=510050")
    assert len(risk) == 16 * 2 + 17 * 2  # the September contracts are not listed on their expiry day
    sse_bars = read_canonical(tmp_path, "option_daily_bars", "underlying=510050")
    assert len(sse_bars) == 4 * 17 and sse_bars["trade_date"].max() == date(2026, 9, 23)
    assert sse_bars["volume"].isna().all()  # Sina's volume field is not used
    assert len(read_canonical(tmp_path, "option_daily_bars", "underlying=IO")) == 14 * 2
    assert len(read_canonical(tmp_path, "option_daily_bars", "underlying=MO")) == 17
    minutes = read_canonical(tmp_path, "option_minute_bars", "trade_date=2026-09-23")
    assert sorted(minutes["contract"].unique()) == ["10009001", "10009002", "10009003", "10009004"]
    totals = read_canonical(tmp_path, "option_minute_totals")
    assert len(totals) == 4 and set(totals["volume"]) == {2 * 239} and set(totals["open_interest"]) == {300}
    assert set(totals["minutes"]) == {240} and set(totals["trade_date"]) == {date(2026, 9, 23)}
    assert not issues(tmp_path, report)["dataset"].str.startswith("option").any()

    provider.calls.clear()
    provider.today = date(2026, 9, 24)
    morning(tmp_path, provider, date(2026, 9, 26))  # Saturday; 09-25 is closed
    assert calls(provider, "risk") == ["20260924"] and calls(provider, "cffex") == ["20260924"]
    assert sorted(calls(provider, "daily")) == ["10009003", "10009004"]  # the September ones are complete
    assert sorted(calls(provider, "minute")) == ["10009003", "10009004"]
    assert read_canonical(tmp_path, "option_daily_bars", "underlying=510050")["trade_date"].max() == date(2026, 9, 24)
    assert len(read_canonical(tmp_path, "option_minute_totals")) == 4 + 2

    provider.calls.clear()
    morning(tmp_path, provider, date(2026, 9, 26))  # a rerun: nothing new
    assert not any(name.startswith("option_") for name, _ in provider.calls)


def test_failures_and_unpublished_sessions_are_fetched_again(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 22), options=True, option_failing={"risk:20260921", "daily:10009003"})
    report = morning(tmp_path, provider, date(2026, 9, 24))  # 09-23 is not published yet
    log = read_canonical(tmp_path, "option_fetch_log")
    windows = set(log.loc[log["dataset"] == "sse_risk", "window"])
    assert "2026-09-21" not in windows and "2026-09-23" not in windows and "2026-09-22" in windows
    assert "2026-09-23" not in set(log.loc[log["dataset"] == "cffex_daily", "window"])
    found = issues(tmp_path, report)
    assert found.loc[found["dataset"] == "options", "severity"].tolist() == ["warning"]

    provider.calls.clear()
    provider.option_failing.clear()
    provider.today = date(2026, 9, 23)
    morning(tmp_path, provider, date(2026, 9, 24))
    assert calls(provider, "risk") == ["20260921", "20260923"] and calls(provider, "cffex") == ["20260923"]
    assert "10009003" in calls(provider, "daily")


def test_minutes_stop_once_the_source_has_moved_to_the_next_session(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 23), options=True, option_minute_shift=1)
    report = morning(tmp_path, provider, date(2026, 9, 24))
    assert len(calls(provider, "minute")) == 3  # then the rest are given up
    assert not canonical_path(tmp_path, "option_minute_bars", "trade_date=2026-09-23").exists()
    issue = issues(tmp_path, report).set_index("dataset").loc["option_minute_bars"]
    assert issue["severity"] == "warning" and "4 个合约" in issue["message"]


def test_the_options_step_runs_only_when_asked_and_the_rebuild_agrees(tmp_path: Path) -> None:
    provider = FakeProvider(today=date(2026, 9, 23), options=True)
    IngestionPipeline(tmp_path, make_config(), provider=provider, clock=lambda: at(date(2026, 9, 24), 9, 5)).run()
    assert not calls(provider, "risk")  # not among the default steps
    morning(tmp_path, provider, date(2026, 9, 24))
    provider.today = date(2026, 9, 24)
    morning(tmp_path, provider, date(2026, 9, 26))
    report = rebuild_canonical(tmp_path, make_config())
    for dataset in OPTION_DATASETS:
        assert report["diff"][dataset]["live"] == report["diff"][dataset]["rebuilt"], dataset
