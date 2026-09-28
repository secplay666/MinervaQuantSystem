"""Helpers shared by routers: per-account costs and trading rules."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from ..backtest.config import BacktestConfig
from ..decision.inputs import exchange_calendar, latest_ingest
from ..domain.fees import FeeSchedule
from ..domain.rules import MarketRules
from .db.models import Account


@lru_cache(maxsize=16)
def _config(root: str, relative: str, mtime: float) -> BacktestConfig:
    path = Path(root) / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BacktestConfig.from_payload(payload, Path(root))


@lru_cache(maxsize=4)
def _rules(path: str, mtime: float) -> MarketRules:
    return MarketRules.load(Path(path))


def account_config(root: Path, account: Account) -> BacktestConfig:
    path = root / account.strategy_config
    return _config(str(root), account.strategy_config, path.stat().st_mtime)


def account_costs(root: Path, account: Account) -> tuple[FeeSchedule, MarketRules]:
    config = account_config(root, account)
    rules = _rules(str(config.market_rules_path), config.market_rules_path.stat().st_mtime)
    return FeeSchedule(rules, config.commission_rate, config.commission_min_fen), rules


@lru_cache(maxsize=2)
def _sessions(root: str, run_id: str) -> tuple[date, ...]:
    return tuple(exchange_calendar(Path(root), run_id))


def exchange_sessions(root: Path) -> tuple[date, ...] | None:
    """Exchange sessions, future ones included, from the newest ingest's raw
    calendar; None when unavailable (callers then treat weekdays as sessions)."""
    try:
        record = latest_ingest(root)
        return _sessions(str(root), record.run_id) if record is not None else None
    except (OSError, ValueError, KeyError):
        return None


def parse_day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
