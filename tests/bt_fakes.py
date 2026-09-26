"""Synthetic market data and strategies for backtest tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quant_system.backtest.config import BacktestConfig
from quant_system.data_platform.symbols import infer_board, infer_exchange
from quant_system.backtest.market_data import MarketData
from quant_system.domain.entities import TargetPortfolio
from quant_system.domain.rules import MarketRules

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "configs" / "market_rules" / "cn_a_share.json"


def rules() -> MarketRules:
    return MarketRules.load(RULES_PATH)


def weekdays(start: date, count: int) -> list[date]:
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@dataclass
class SyntheticMarket:
    """Build MarketData from per-symbol close paths.

    ``closes[symbol]`` is a list of closes (None = suspended) aligned with
    ``sessions``; open defaults to the previous close, high/low bracket
    open and close.  ``factors[symbol]`` lists (date, hfq) events.
    """

    sessions: list[date]
    closes: dict[str, list[float | None]]
    boards: dict[str, str] = field(default_factory=dict)
    list_dates: dict[str, date | None] = field(default_factory=dict)
    delist_dates: dict[str, date | None] = field(default_factory=dict)
    opens: dict[str, dict[int, float]] = field(default_factory=dict)
    highs: dict[str, dict[int, float]] = field(default_factory=dict)
    lows: dict[str, dict[int, float]] = field(default_factory=dict)
    volumes: dict[str, float] = field(default_factory=dict)
    factors: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    risk: list[dict[str, Any]] = field(default_factory=list)

    def frames(self) -> dict[str, pd.DataFrame]:
        rows = []
        for symbol, path in self.closes.items():
            previous = None
            for index, (day, close) in enumerate(zip(self.sessions, path)):
                if close is None:
                    continue
                open_ = self.opens.get(symbol, {}).get(index, previous if previous is not None else close)
                high = self.highs.get(symbol, {}).get(index, max(open_, close))
                low = self.lows.get(symbol, {}).get(index, min(open_, close))
                volume = self.volumes.get(symbol, 10_000_000.0)
                rows.append({"symbol": symbol, "trade_date": day, "open": open_, "high": high, "low": low,
                             "close": close, "volume_shares": volume, "turnover_cny": volume * close})
                previous = close
        master = pd.DataFrame([
            {"symbol": symbol, "board": self.boards.get(symbol, _board(symbol)), "exchange": _exchange(symbol),
             "list_date": self.list_dates.get(symbol, date(2000, 1, 4)),
             "delist_date": self.delist_dates.get(symbol)}
            for symbol in self.closes
        ])
        factors = pd.DataFrame(
            [{"symbol": symbol, "effective_date": date(1900, 1, 1), "hfq_factor": 1.0} for symbol in self.closes]
            + [{"symbol": symbol, "effective_date": day, "hfq_factor": value}
               for symbol, events in self.factors.items() for day, value in events]
        )
        risk = pd.DataFrame(self.risk, columns=["symbol", "status", "start_date", "end_date", "method", "source"])
        indices = pd.DataFrame(
            [{"symbol": "sh000300", "trade_date": day, "close": 4000.0 + index} for index, day in enumerate(self.sessions)]
        )
        return {"calendar": pd.DataFrame({"trade_date": self.sessions}), "bars": pd.DataFrame(rows),
                "factors": factors, "master": master, "risk": risk, "indices": indices}

    def build(self) -> MarketData:
        return MarketData.from_frames(self.frames(), {"data_version": "synthetic"})


def _board(symbol: str) -> str:
    return infer_board(symbol)


def _exchange(symbol: str) -> str:
    return infer_exchange(symbol)


@dataclass
class FixedStrategy:
    """Targets given per signal session; eligible = has a bar."""

    targets: dict[date, dict[str, float]]
    strategy_id: str = "fixed"
    version: str = "1"

    def on_close(self, view) -> TargetPortfolio:
        weights = self.targets.get(view.session)
        if weights is None:
            raise AssertionError(f"strategy called on a non-signal session {view.session}")
        ranks = {symbol: {"rank": rank + 1} for rank, symbol in enumerate(sorted(weights))}
        return TargetPortfolio(view.session, dict(weights), ranks)

    def eligible(self, view):
        return view.has_bar()


@dataclass
class DatesSchedule:
    dates: set[date]

    def is_rebalance(self, calendar, index: int) -> bool:
        return calendar.sessions[index] in self.dates


def config(**overrides: Any) -> BacktestConfig:
    payload: dict[str, Any] = {
        "name": "test",
        "run": {"start": "2024-01-02", "end": "2024-12-31", "initial_capital_cny": "1000000"},
        "data": {"source": "parquet_dir", "path": "unused"},
        "market_rules": str(RULES_PATH),
        "strategy": {"id": "fixed", "version": "1", "params": {}},
        "portfolio": {"cash_buffer": 0.0, "band_rel": 0.0, "min_lot_policy": "skip"},
        "execution": {"price": "open", "slippage_bps": "0", "max_participation": 1.0,
                      "buy_retry_sessions": 5, "delay_sessions": 1},
        "costs": {"commission_rate": "0.00025", "commission_min_cny": "5"},
        "delisting": {"settlement": "last_close"},
        "benchmarks": {"indices": [], "equal_weight_universe": False},
    }
    for key, value in overrides.items():
        section, _, name = key.partition("__")
        if name:
            payload[section] = {**payload[section], name: value}
        else:
            payload[section] = value
    return BacktestConfig.from_payload(payload, ROOT)
