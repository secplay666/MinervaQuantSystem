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
    def fetch_daily_bars(
        self, symbol: str, start_date: str, end_date: str, delisted: bool = False
    ) -> FetchResult:
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

    @abstractmethod
    def fetch_sse_bulletins(self, title: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def fetch_bse_announcements(self, keyword: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError

    # -- stage 3: corporate data, classification, total-return indices ---------

    @abstractmethod
    def fetch_csindex_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        """CSIndex daily history (e.g. total-return H00300), YYYYMMDD bounds."""
        raise NotImplementedError

    @abstractmethod
    def fetch_index_weights(self, symbol: str) -> pd.DataFrame:
        """Latest published CSIndex constituent weights (no history)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_sw_classification(self) -> dict[str, pd.DataFrame]:
        """Shenwan workbooks: ``history`` (dated stock classes) and ``codes``."""
        raise NotImplementedError

    @abstractmethod
    def fetch_share_capital(self, date_field: str, start: str, end: str) -> pd.DataFrame:
        """Share-capital changes with ``date_field`` (END_DATE or NOTICE_DATE)
        in [start, end) (YYYY-MM-DD)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_financial_statement(self, statement: str, company_type: str, date_field: str, start: str,
                                  end: str) -> pd.DataFrame:
        """One statement (income/balance/cashflow) for one company type
        (G/B/S/I) with ``date_field`` (REPORT_DATE or UPDATE_DATE) in [start, end)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_dividends(self, report_date: str) -> pd.DataFrame:
        """Dividend and bonus-share plans for one report period (YYYY-MM-DD)."""
        raise NotImplementedError
