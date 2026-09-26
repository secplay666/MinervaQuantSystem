from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import requests

from quant_system.data_platform.providers.akshare_provider import (
    AkShareProvider,
    ProviderError,
    _is_terminal,
    derive_hfq_events_from_prevclose,
    install_default_timeout,
)
from quant_system.data_platform.quality import (
    validate_adjustment_factors,
    validate_bars,
    validate_security_master,
    validate_volume_units,
)
from quant_system.data_platform.storage import (
    build_duckdb_catalog,
    canonical_inventory,
    write_canonical_frame,
)

# quality -------------------------------------------------------------------------


def _bars(**overrides: object) -> pd.DataFrame:
    row = {"symbol": "000001", "trade_date": date(2026, 7, 24), "open": 10.0, "high": 10.6, "low": 9.9,
           "close": 10.5, "volume_shares": 100_000, "turnover_cny": 1_040_000.0}
    row.update(overrides)
    return pd.DataFrame([row])


def test_quality_detects_invalid_ohlc_and_zero_prices() -> None:
    rules = {issue.rule for issue in validate_bars(_bars(high=9.0), "daily_bars", "000001")}
    assert "valid_ohlc_relationship" in rules
    rules = {issue.rule for issue in validate_bars(_bars(open=0.0), "daily_bars", "000001")}
    assert "positive_prices" in rules


def test_volume_in_lots_is_blocking() -> None:
    assert validate_volume_units(_bars(), "000001") == []
    issues = validate_volume_units(_bars(volume_shares=1_000), "000001")
    assert issues and issues[0].severity == "blocking"


def test_factor_history_revision_is_reported() -> None:
    previous = pd.DataFrame({"symbol": "X", "effective_date": [date(1900, 1, 1), date(2020, 1, 1)],
                             "hfq_factor": [1.0, 1.5]})
    current = previous.assign(hfq_factor=[1.0, 1.6])
    rules = {issue.rule for issue in validate_adjustment_factors(current, "X", previous, date(2020, 1, 2))}
    assert "history_stable" in rules
    assert validate_adjustment_factors(previous, "X", previous, date(2020, 1, 2)) == []


def test_listing_shrink_blocks() -> None:
    previous = pd.DataFrame({"symbol": [f"{i:06d}" for i in range(100)], "status": "listed"})
    current = previous.iloc[:90].assign(exchange="SZSE", list_date=date(2000, 1, 1))
    issues = validate_security_master(current, previous, 0.02)
    assert any(issue.rule == "listing_not_shrunk" and issue.severity == "blocking" for issue in issues)


# storage / adjusted view ---------------------------------------------------------------


def test_adjusted_view_multiplies_hfq_and_anchors_qfq(tmp_path: Path) -> None:
    days = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    bars = pd.DataFrame({"symbol": "600001", "trade_date": days, "open": [20.0, 10.0, 10.0],
                         "high": [20.0, 10.0, 10.0], "low": [20.0, 10.0, 10.0], "close": [20.0, 10.0, 10.2]})
    write_canonical_frame(tmp_path, "daily_bars", bars, partition="symbol=600001")
    write_canonical_frame(tmp_path, "daily_bars", bars.assign(symbol="600009"), partition="symbol=600009")
    factors = pd.DataFrame({"symbol": "600001", "effective_date": [date(1900, 1, 1), date(2026, 9, 22)],
                            "hfq_factor": [1.0, 2.0], "qfq_factor": [0.5, 1.0],
                            "factor_as_of": date(2026, 9, 22)})
    write_canonical_frame(tmp_path, "adjustment_factors", factors, partition="symbol=600001")
    build_duckdb_catalog(tmp_path)
    with duckdb.connect(str(tmp_path / "data" / "market.duckdb"), read_only=True) as con:
        view = con.execute(
            "SELECT symbol, trade_date, hfq_close, qfq_close, factor_status FROM daily_bars_adjusted "
            "ORDER BY symbol, trade_date"
        ).fetchdf()
    ours = view[view["symbol"] == "600001"]
    assert ours["hfq_close"].tolist() == [20.0, 20.0, 20.4]
    assert ours["qfq_close"].tolist() == [10.0, 10.0, 10.2]
    assert ours["factor_status"].tolist() == ["ok", "ok", "stale"]
    missing = view[view["symbol"] == "600009"]
    assert missing["hfq_close"].isna().all() and set(missing["factor_status"]) == {"missing"}


def test_canonical_write_refuses_empty_frames(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_canonical_frame(tmp_path, "index_bars", pd.DataFrame(), partition="symbol=sh000001")


def test_data_version_changes_with_content(tmp_path: Path) -> None:
    write_canonical_frame(tmp_path, "trading_calendar", pd.DataFrame({"trade_date": [date(2026, 9, 24)]}))
    inventory, first = canonical_inventory(tmp_path)
    assert inventory.loc[0, "max_date"] == "2026-09-24"
    write_canonical_frame(tmp_path, "trading_calendar", pd.DataFrame({"trade_date": [date(2026, 9, 28)]}))
    assert canonical_inventory(tmp_path)[1] != first


# provider helpers --------------------------------------------------------------------------


def test_default_timeout_applies_only_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(args[6] if len(args) > 6 else kwargs.get("timeout"))

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    monkeypatch.setattr(requests.sessions.Session, "_quant_system_timeout_wrappers", (), raising=False)
    install_default_timeout(7.0)
    wrapped = requests.sessions.Session.request
    install_default_timeout(5.0)  # idempotent: updates the default, no stacking
    assert requests.sessions.Session.request is wrapped
    session = requests.Session()
    session.request("GET", "http://example.invalid")
    session.request("GET", "http://example.invalid", timeout=3)
    session.request("GET", "http://example.invalid", None, None, None, None, None, None, None)
    assert seen == [5.0, 3, 5.0]


def test_error_classification() -> None:
    assert not _is_terminal(requests.ConnectionError("reset"))
    assert not _is_terminal(ValueError.__new__(__import__("json").JSONDecodeError))
    assert _is_terminal(KeyError("day"))
    assert _is_terminal(ValueError("sina hfq factor not available"))


def test_call_does_not_retry_terminal_errors() -> None:
    provider = AkShareProvider.__new__(AkShareProvider)
    provider.max_retries, provider.request_pause_seconds = 4, 0
    provider.logger = __import__("logging").getLogger("test")
    attempts: list[int] = []

    def broken() -> None:
        attempts.append(1)
        raise KeyError("day")

    with pytest.raises(ProviderError) as info:
        provider._call("broken", broken)
    assert info.value.terminal and len(attempts) == 1


def test_prevclose_derivation_matches_proportional_factors() -> None:
    # Cash dividend of 1.0 on 2026-06-26: reference price 99, prior close 100.
    history = pd.DataFrame({
        "date": ["2026-06-24", "2026-06-25", "2026-06-26", "2026-06-29"],
        "close": [101.0, 100.0, 99.5, 99.0],
        "prevclose": [None, None, 99.0, None],
    })
    frame = derive_hfq_events_from_prevclose(history)
    assert frame["date"].dt.date.tolist() == [date(1900, 1, 1), date(2026, 6, 26)]
    assert frame["hfq_factor"].tolist() == pytest.approx([1.0, 100.0 / 99.0])
    with pytest.raises(ProviderError):
        derive_hfq_events_from_prevclose(history.drop(columns=["prevclose"]))


def test_volume_unit_rule_checks_each_ingestion_run() -> None:
    history = pd.concat([_bars(run_id="r1")] * 50, ignore_index=True)
    appended = _bars(run_id="r2", volume_shares=1_000)  # one new row, in lots
    issues = validate_volume_units(pd.concat([history, appended], ignore_index=True), "000001")
    assert issues and "r2" in issues[0].message
