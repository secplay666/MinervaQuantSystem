"""Golden regression for the multi-factor pipeline on a committed real-data
fixture (factors, universe, rule-based construction, risk tables, factor
evaluation).  Update only on purpose, after reviewing the diff:

    UPDATE_GOLDEN=1 .venv/Scripts/python.exe -m pytest tests/test_mf_regression.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_system.backtest.config import BacktestConfig
from quant_system.backtest.market_data import load_market_data
from quant_system.backtest.runner import run_backtest
from quant_system.features.store import FactorCache
from quant_system.research.data import load_research_data
from quant_system.research.experiments import ResearchConfig, run_factor_evaluation
from quant_system.research.registry import Registry
from quant_system.strategy.registry import StrategyContext

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "multifactor_cn"
GOLDEN = FIXTURE / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

pytestmark = pytest.mark.skipif(not (FIXTURE / "SOURCE.json").exists(), reason="multi-factor fixture not extracted")

TABLES = {
    "nav": ["session", "cash_fen", "market_value_fen", "nav_fen", "fees_cum_fen", "positions"],
    "fills": ["session", "symbol", "side", "quantity", "price_fen", "notional_fen", "commission_fen",
              "stamp_duty_fen", "transfer_fee_fen"],
    "positions": ["session", "symbol", "quantity", "mark_fen"],
    "signals": ["as_of", "symbol", "weight", "rank", "score", "industry"],
}


@pytest.fixture(scope="module")
def loaded():
    market = load_market_data("parquet_dir", FIXTURE)
    return market, load_research_data("parquet_dir", FIXTURE, market=market)


@pytest.fixture(scope="module")
def backtest(loaded):
    market, research = loaded
    config = BacktestConfig.load(ROOT / "configs" / "strategies" / "golden_multifactor.json", ROOT)
    return run_backtest(config, market, StrategyContext(ROOT, market, lambda: research))


def _compare(name: str, frame: pd.DataFrame) -> None:
    path = GOLDEN / f"{name}.csv"
    if UPDATE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False, lineterminator="\n", float_format="%.10g")
        return
    expected = pd.read_csv(path, dtype={"symbol": str, "session": str, "as_of": str, "industry": str})
    actual = pd.read_csv(pd.io.common.StringIO(frame.to_csv(index=False, float_format="%.10g")),
                         dtype={"symbol": str, "session": str, "as_of": str, "industry": str})
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-8, obj=name)


def test_multifactor_backtest_matches_golden(backtest) -> None:
    result, summary, _ = backtest
    assert result.checks["replay_matches"]
    for name, columns in TABLES.items():
        frame = getattr(result, name)[columns].astype({columns[0]: str})
        _compare(name, frame)
    for name in ("style_exposures", "concentration", "brinson"):
        frame = result.extras[name].astype({"session": str})
        _compare(name, frame.round(10))
    metrics = {key: summary["performance"]["策略"][key] for key in ("total_return", "cagr", "sharpe", "max_drawdown")}
    path = GOLDEN / "summary.json"
    current = {"fingerprint": summary["data"]["fingerprint"], "counters": summary["counters"],
               "performance": {k: round(float(v), 9) for k, v in metrics.items()}}
    if UPDATE:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert current == json.loads(path.read_text(encoding="utf-8"))


def test_factor_evaluation_matches_golden(loaded, tmp_path) -> None:
    market, research = loaded
    config = ResearchConfig.load(ROOT / "configs" / "research" / "golden_factors.json", ROOT)
    _, results = run_factor_evaluation(config, "IS", research=research, registry=Registry(tmp_path / "r.sqlite"),
                                       root=tmp_path, cache=FactorCache(None))
    summary = results["summary"].drop(columns=["significant"]).round(10)
    _compare("factor_summary", summary)
    assert np.isfinite(results["summary"]["rank_ic_mean"]).sum() >= 20  # fundamentals present in the fixture
