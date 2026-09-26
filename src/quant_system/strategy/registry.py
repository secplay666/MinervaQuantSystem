"""Strategy registry: ``strategy.id`` in a config -> factory.

A factory receives the parsed config and a ``StrategyContext`` (the loaded
market data plus lazily loaded research data), so strategies that need
fundamentals or factors do not force every backtest to load them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

StrategyFactory = Callable[[Any, "StrategyContext"], Any]
_FACTORIES: dict[str, StrategyFactory] = {}


@dataclass
class StrategyContext:
    root: Path
    data: Any  # backtest.market_data.MarketData
    research_loader: Callable[[], Any] | None = None
    _research: Any = field(default=None, init=False, repr=False)

    def research(self) -> Any:
        if self._research is None:
            if self.research_loader is None:
                raise ValueError("this strategy needs research data but none is configured")
            self._research = self.research_loader()
        return self._research


def register_strategy(strategy_id: str) -> Callable[[StrategyFactory], StrategyFactory]:
    def decorator(factory: StrategyFactory) -> StrategyFactory:
        if strategy_id in _FACTORIES:
            raise ValueError(f"strategy {strategy_id!r} registered twice")
        _FACTORIES[strategy_id] = factory
        return factory

    return decorator


def build_strategy(config, context: StrategyContext):
    _load_builtin()
    try:
        factory = _FACTORIES[config.strategy_id]
    except KeyError:
        raise ValueError(f"unknown strategy {config.strategy_id!r}; known: {sorted(_FACTORIES)}") from None
    return factory(config, context)


def registered() -> list[str]:
    _load_builtin()
    return sorted(_FACTORIES)


def _load_builtin() -> None:
    # Importing the modules runs their @register_strategy decorators.
    from . import momentum  # noqa: F401
