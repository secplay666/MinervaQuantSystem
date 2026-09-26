"""Golden regression on the committed real-data fixture (ARCHITECTURE §17.3).

Update the golden files only on purpose, after reviewing the diff:

    UPDATE_GOLDEN=1 .venv/Scripts/python.exe -m pytest tests/test_bt_regression.py
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pandas as pd
import pytest

from quant_system.backtest.config import BacktestConfig
from quant_system.backtest.runner import run_backtest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "backtest_cn_small" / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

TABLES = {
    "nav": ["session", "cash_fen", "market_value_fen", "nav_fen", "fees_cum_fen", "positions"],
    "fills": ["session", "symbol", "side", "quantity", "price_fen", "notional_fen", "commission_fen",
              "stamp_duty_fen", "transfer_fee_fen"],
    "positions": ["session", "symbol", "quantity", "mark_fen"],
    "rejections": ["session", "symbol", "side", "rule_id", "decision"],
    "corporate_actions": ["session", "symbol", "old_quantity", "new_quantity", "cash_in_lieu_fen"],
}


def _summary(summary: dict) -> dict:
    def clean(value):
        if isinstance(value, float):
            return None if math.isnan(value) else round(value, 9)
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value if isinstance(value, (int, str, type(None), list)) else str(value)

    return clean({
        "fingerprint": summary["data"]["fingerprint"],
        "performance": summary["performance"],
        "relative": summary["relative"],
        "trading": summary["trading"],
        "counters": summary["counters"],
    })


@pytest.fixture(scope="module")
def golden_run():
    config = BacktestConfig.load(ROOT / "configs" / "strategies" / "golden_small.json", ROOT)
    return run_backtest(config)


def test_golden_backtest_matches(golden_run) -> None:
    result, summary, _ = golden_run
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, columns in TABLES.items():
        frame = getattr(result, name)
        frame = frame[columns].astype({"session": str}) if not frame.empty else pd.DataFrame(columns=columns)
        path = GOLDEN / f"{name}.csv"
        if UPDATE:
            frame.to_csv(path, index=False, lineterminator="\n")
            continue
        expected = pd.read_csv(path, dtype={"symbol": str, "session": str})
        actual = pd.read_csv(pd.io.common.StringIO(frame.to_csv(index=False)), dtype={"symbol": str, "session": str})
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False, obj=name)
    path = GOLDEN / "summary.json"
    current = _summary(summary)
    if UPDATE:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert current == json.loads(path.read_text(encoding="utf-8"))


def test_golden_run_passes_accounting_checks(golden_run) -> None:
    result, summary, _ = golden_run
    assert result.checks["replay_matches"]
    assert summary["counters"]["fills"] > 100
    # The fixture's special cases must actually be exercised.
    assert summary["counters"].get("corporate_actions", 0) > 0
    assert any(key.startswith("rejected_") for key in summary["counters"])


@pytest.mark.slow
def test_full_market_smoke() -> None:
    database = ROOT / "data" / "market.duckdb"
    if not database.exists():
        pytest.skip("local market database not available")
    config = BacktestConfig.load(ROOT / "configs" / "strategies" / "momentum_top50.json", ROOT)
    result, summary, _ = run_backtest(config)
    assert result.checks["replay_matches"]
    assert summary["counters"]["fills"] > 1000
    assert summary["performance"]["策略"]["sessions"] > 1000
    assert summary["timings"]["engine_seconds"] < 60
