from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FetchResult:
    """A vendor response and the endpoint that actually produced it."""

    frame: pd.DataFrame
    source: str


class MarketDataProvider(ABC):
    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def fetch_trading_calendar(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_security_lists(self) -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    @abstractmethod
    def fetch_daily_bars(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_adjustment_factors(self, symbol: str) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_market_snapshot(self) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_suspension_snapshot(self, date: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_suspension_events(self, date: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_sz_name_changes(self) -> pd.DataFrame:
        raise NotImplementedError
