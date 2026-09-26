from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..data_platform.utils import json_hash
from ..domain.money import rate, yuan_to_fen
from ..portfolio.sizing import SizingPolicy


@dataclass(frozen=True)
class BacktestConfig:
    name: str
    start: date
    end: date
    initial_capital_fen: int
    data_source: str
    data_path: Path
    market_rules_path: Path
    strategy_id: str
    strategy_version: str
    strategy_params: dict[str, Any]
    sizing: SizingPolicy
    slippage: Fraction
    max_participation: float
    buy_retry_sessions: int
    delay_sessions: int
    commission_rate: Fraction
    commission_min_fen: int
    delisting_settlement: str
    index_benchmarks: tuple[str, ...]
    equal_weight_benchmark: bool
    risk_free_rate: float
    periods_per_year: int
    schedule: dict[str, Any] = field(default_factory=lambda: {"type": "month_end"})
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        return json_hash(self.payload)

    @classmethod
    def load(cls, path: Path, root: Path) -> "BacktestConfig":
        return cls.from_payload(json.loads(path.read_text(encoding="utf-8")), root)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], root: Path) -> "BacktestConfig":
        run = payload["run"]
        data = payload["data"]
        portfolio = payload.get("portfolio", {})
        execution = payload.get("execution", {})
        costs = payload["costs"]
        benchmarks = payload.get("benchmarks", {})
        analytics = payload.get("analytics", {})
        if execution.get("price", "open") != "open":
            raise ValueError("only execution.price = 'open' is implemented (ADR-002)")
        settlement = payload.get("delisting", {}).get("settlement", "last_close")
        if settlement not in {"last_close", "zero"}:
            raise ValueError("delisting.settlement must be last_close or zero")
        delay = int(execution.get("delay_sessions", 1))
        if delay < 1:
            raise ValueError("execution.delay_sessions must be >= 1 (no same-bar fills)")
        return cls(
            name=payload["name"],
            start=date.fromisoformat(run["start"]),
            end=date.fromisoformat(run["end"]),
            initial_capital_fen=yuan_to_fen(run["initial_capital_cny"]),
            data_source=data["source"],
            data_path=(root / data["path"]).resolve(),
            market_rules_path=(root / payload["market_rules"]).resolve(),
            strategy_id=payload["strategy"]["id"],
            strategy_version=str(payload["strategy"].get("version", "1")),
            strategy_params=dict(payload["strategy"].get("params", {})),
            sizing=SizingPolicy(
                cash_buffer=float(portfolio.get("cash_buffer", 0.005)),
                band_rel=float(portfolio.get("band_rel", 0.25)),
                min_lot_policy=str(portfolio.get("min_lot_policy", "skip")),
            ),
            slippage=rate(execution.get("slippage_bps", "5")) / 10_000,
            max_participation=float(execution.get("max_participation", 0.10)),
            buy_retry_sessions=int(execution.get("buy_retry_sessions", 5)),
            delay_sessions=delay,
            commission_rate=rate(costs["commission_rate"]),
            commission_min_fen=yuan_to_fen(costs.get("commission_min_cny", "5")),
            delisting_settlement=settlement,
            index_benchmarks=tuple(benchmarks.get("indices", ())),
            equal_weight_benchmark=bool(benchmarks.get("equal_weight_universe", True)),
            risk_free_rate=float(analytics.get("risk_free_rate", 0.0)),
            periods_per_year=int(analytics.get("periods_per_year", 244)),
            schedule=dict(payload.get("schedule", {"type": "month_end"})),
            payload=payload,
        )
