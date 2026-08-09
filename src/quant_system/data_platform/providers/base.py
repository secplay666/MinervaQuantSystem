from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_instruments(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_trading_calendar(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_daily_bars(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_index_daily(self, symbol: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_market_snapshot(self) -> pd.DataFrame:
        raise NotImplementedError

