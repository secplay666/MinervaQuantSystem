"""Helpers shared by routers: per-account costs and trading rules."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from ..backtest.config import BacktestConfig
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


def parse_day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
