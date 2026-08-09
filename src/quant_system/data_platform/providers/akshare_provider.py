from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

import akshare as ak
import pandas as pd

from .base import MarketDataProvider

T = TypeVar("T")


class AkShareProvider(MarketDataProvider):
    """AKShare adapter with retries and a stable internal interface."""

    def __init__(
        self,
        expected_version: str,
        max_retries: int = 4,
        request_pause_seconds: float = 0.35,
    ) -> None:
        self.expected_version = expected_version
        self.max_retries = max_retries
        self.request_pause_seconds = request_pause_seconds
        self.logger = logging.getLogger(self.__class__.__name__)
        actual_version = getattr(ak, "__version__", "unknown")
        self.daily_sources: dict[str, str] = {}
        self.index_sources: dict[str, str] = {}
        self.snapshot_source = "akshare.unknown"
        self._eastmoney_daily_available = True
        self._eastmoney_index_available = True
        self._source_lock = threading.Lock()
        if actual_version != expected_version:
            raise RuntimeError(
                f"AKShare version mismatch: expected {expected_version}, "
                f"found {actual_version}. Pin the tested version before ingestion."
            )

    @property
    def version(self) -> str:
        return str(ak.__version__)

    @staticmethod
    def _with_market_prefix(symbol: str) -> str:
        if symbol.startswith(("sh", "sz", "bj")):
            return symbol
        if symbol.startswith(("5", "6")):
            return f"sh{symbol}"
        if symbol.startswith(("4", "8", "9")):
            return f"bj{symbol}"
        return f"sz{symbol}"

    def _call(self, name: str, func: Callable[..., T], **kwargs: object) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = func(**kwargs)
                if self.request_pause_seconds:
                    time.sleep(self.request_pause_seconds)
                return result
            except Exception as exc:  # upstream errors are heterogeneous
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = min(2 ** (attempt - 1), 8)
                self.logger.warning(
                    "%s failed on attempt %s/%s: %s; retrying in %ss",
                    name,
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError(f"AKShare call {name} failed: {last_error}") from last_error

    def fetch_instruments(self) -> pd.DataFrame:
        return self._call("stock_info_a_code_name", ak.stock_info_a_code_name)

    def fetch_trading_calendar(self) -> pd.DataFrame:
        return self._call("tool_trade_date_hist_sina", ak.tool_trade_date_hist_sina)

    def fetch_daily_bars(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        if symbol.startswith(("4", "8", "9")):
            frame = self._call(
                "stock_zh_a_daily_sina",
                ak.stock_zh_a_daily,
                symbol=self._with_market_prefix(symbol),
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            self.daily_sources[symbol] = "akshare.stock_zh_a_daily.sina"
            return frame
        with self._source_lock:
            eastmoney_available = self._eastmoney_daily_available
        if eastmoney_available:
            try:
                frame = self._call(
                    "stock_zh_a_hist",
                    ak.stock_zh_a_hist,
                    symbol=symbol,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                    timeout=30,
                )
                self.daily_sources[symbol] = "akshare.stock_zh_a_hist.eastmoney"
                return frame
            except RuntimeError as exc:
                with self._source_lock:
                    self._eastmoney_daily_available = False
                self.logger.warning(
                    "Eastmoney daily bars unavailable; using Tencent for the "
                    "remaining run. First affected symbol=%s: %s",
                    symbol,
                    exc,
                )
        frame = self._call(
            "stock_zh_a_hist_tx",
            ak.stock_zh_a_hist_tx,
            symbol=self._with_market_prefix(symbol),
            start_date=start_date,
            end_date=end_date,
            adjust="",
            timeout=30,
        )
        self.daily_sources[symbol] = "akshare.stock_zh_a_hist_tx.tencent"
        return frame

    def fetch_adjustment_factors(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        provider_symbol = self._with_market_prefix(symbol)
        qfq = self._call(
            "stock_zh_a_daily_qfq_factor",
            ak.stock_zh_a_daily,
            symbol=provider_symbol,
            start_date="19900101",
            end_date=end_date,
            adjust="qfq-factor",
        )
        hfq = self._call(
            "stock_zh_a_daily_hfq_factor",
            ak.stock_zh_a_daily,
            symbol=provider_symbol,
            start_date="19900101",
            end_date=end_date,
            adjust="hfq-factor",
        )
        qfq = qfq.rename(columns={"qfq_factor": "qfq_factor"})
        hfq = hfq.rename(columns={"hfq_factor": "hfq_factor"})
        if "date" not in qfq.columns or "date" not in hfq.columns:
            raise RuntimeError(
                f"Unexpected adjustment factor columns for {symbol}: "
                f"qfq={list(qfq.columns)}, hfq={list(hfq.columns)}"
            )
        frame = pd.merge(qfq, hfq, on="date", how="outer")
        frame.insert(0, "symbol", symbol)
        return frame

    def fetch_index_daily(self, symbol: str) -> pd.DataFrame:
        with self._source_lock:
            eastmoney_available = self._eastmoney_index_available
        if eastmoney_available:
            try:
                frame = self._call(
                    "stock_zh_index_daily_em",
                    ak.stock_zh_index_daily_em,
                    symbol=symbol,
                )
                self.index_sources[symbol] = (
                    "akshare.stock_zh_index_daily_em.eastmoney"
                )
                return frame
            except RuntimeError as exc:
                with self._source_lock:
                    self._eastmoney_index_available = False
                self.logger.warning(
                    "Eastmoney index bars unavailable; using Tencent for the "
                    "remaining run. First affected symbol=%s: %s",
                    symbol,
                    exc,
                )
        frame = self._call(
            "stock_zh_a_hist_tx",
            ak.stock_zh_a_hist_tx,
            symbol=self._with_market_prefix(symbol),
            start_date="19900101",
            end_date="20500101",
            adjust="",
            timeout=30,
        )
        self.index_sources[symbol] = "akshare.stock_zh_a_hist_tx.tencent"
        return frame

    def fetch_market_snapshot(self) -> pd.DataFrame:
        try:
            frame = self._call("stock_zh_a_spot_em", ak.stock_zh_a_spot_em)
            self.snapshot_source = "akshare.stock_zh_a_spot_em.eastmoney"
            return frame
        except RuntimeError as exc:
            self.logger.warning(
                "Eastmoney market snapshot unavailable; falling back to Tencent: %s",
                exc,
            )
            frame = self._call("stock_zh_a_spot_tx", ak.stock_zh_a_spot_tx)
            self.snapshot_source = "akshare.stock_zh_a_spot_tx.tencent"
            return frame
