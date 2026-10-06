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

    def fetch_earnings_forecast(self, date_field: str, start: str, end: str) -> pd.DataFrame:
        """Earnings forecasts (业绩预告) with ``date_field`` (REPORT_DATE or
        NOTICE_DATE) in [start, end); optional: providers without it skip the step."""
        raise NotImplementedError

    def fetch_intraday_bars(self, code: str) -> pd.DataFrame:
        """The 1-minute bars the source still keeps (sh/sz codes, or a futures code such as IF0);
        optional: providers without it skip the intraday step."""
        raise NotImplementedError

    def fetch_intraday_trades(self, code: str) -> pd.DataFrame:
        """3-second trades of the latest session, with the active side (optional, see above)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_dividends(self, report_date: str) -> pd.DataFrame:
        """Dividend and bonus-share plans for one report period (YYYY-MM-DD)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_etf_shares_sse(self, trade_date: str) -> pd.DataFrame:
        """SSE ETF shares outstanding on one day (YYYYMMDD), as published:
        STAT_DATE, ETF_TYPE, SEC_CODE, SEC_NAME, TOT_VOL (10,000 shares).
        Empty on a day without data."""
        raise NotImplementedError

    @abstractmethod
    def fetch_etf_lists(self) -> dict[str, pd.DataFrame]:
        """Current ETF lists with each fund's tracking index: ``sse``
        (fundCode, fundAbbr, secNameFull, INDEX_CODE, INDEX_NAME, companyName,
        listingDate) and ``szse`` (证券代码, 证券简称, 拟合指数, 基金管理人)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_fund_reports(self, symbol: str) -> pd.DataFrame:
        """A fund's periodic-report announcements (every page): FUNDCODE,
        TITLE, PUBLISHDATEDesc (YYYY-MM-DD), ID (the announcement code)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_report_text(self, art_code: str) -> str:
        """The text rendering of one announcement."""
        raise NotImplementedError

    @abstractmethod
    def fetch_etf_shares_szse(self, start: str, end: str) -> pd.DataFrame:
        """SZSE ETF shares outstanding per day in [start, end] (YYYYMMDD, at
        most six months): 日期, 基金代码, 基金简称, 基金份额 (shares)."""
        raise NotImplementedError
