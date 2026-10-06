from __future__ import annotations

import functools
import importlib
import io
import json
import logging
import math
import threading
import time
import warnings
from collections.abc import Callable
from typing import TypeVar

import akshare as ak
import pandas as pd
import requests

from ..symbols import infer_exchange, market_prefix
from .base import FetchResult, MarketDataProvider
from .eastmoney_dc import A_SHARE_TYPES, DC_PAGE_WORKERS, PaginationMismatch, collect_pages, dc_page, sw_file

T = TypeVar("T")

_TIMEOUT_LOCK = threading.Lock()
_TIMEOUT_VALUE = "_quant_system_default_timeout"
_TIMEOUT_WRAPPERS = "_quant_system_timeout_wrappers"
# Session.request(self, method, url, params, data, headers, cookies, files, auth, timeout, ...)
_TIMEOUT_POSITION = 6  # index of ``timeout`` in *args after (method, url)


def _has_timeout_wrapper(func: object, ours: tuple[object, ...]) -> bool:
    # Identity along functools.wraps chains; an attribute marker would be
    # copied onto third-party wrappers of our wrapper.
    for _ in range(64):
        if func is None:
            return False
        if any(func is wrapper for wrapper in ours):
            return True
        func = getattr(func, "__wrapped__", None)
    return False


def install_default_timeout(seconds: float) -> None:
    """Give every ``requests`` call whose timeout is None a default one.

    Several AKShare functions call ``requests.get(url)`` without a timeout
    (Sina factors/history, Tencent's start-year lookup even when a timeout is
    passed), so a stalled socket would block a worker forever.
    ``socket.setdefaulttimeout`` does not help because requests passes
    ``timeout=None`` explicitly.  The patch wraps ``Session.request`` once,
    keeps any pre-existing wrapper, and only updates the default on repeated
    calls.  curl_cffi (Baidu endpoints) is a separate client with its own 30 s
    default.  A timeout bounds each connect/read wait, not total duration.
    """
    if seconds is None or seconds <= 0:
        raise ValueError(f"default timeout must be positive, got {seconds!r}")
    session_cls = requests.sessions.Session
    with _TIMEOUT_LOCK:
        setattr(session_cls, _TIMEOUT_VALUE, seconds)
        ours = getattr(session_cls, _TIMEOUT_WRAPPERS, ())
        current = session_cls.request
        if _has_timeout_wrapper(current, ours):
            return

        @functools.wraps(current)
        def request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            default = getattr(requests.sessions.Session, _TIMEOUT_VALUE, None)
            if default is not None:
                if len(args) > _TIMEOUT_POSITION:
                    if args[_TIMEOUT_POSITION] is None:
                        args = args[:_TIMEOUT_POSITION] + (default,) + args[_TIMEOUT_POSITION + 1:]
                elif kwargs.get("timeout") is None:
                    kwargs["timeout"] = default
            return current(self, method, url, *args, **kwargs)

        setattr(session_cls, _TIMEOUT_WRAPPERS, (*ours, request))
        session_cls.request = request


class _FastDemjson:
    """Stands in for the ``demjson`` module inside AKShare's Tencent functions.

    demjson is a pure-Python parser: 0.1-0.5 s per ~65 KB kline response, all
    of it holding the GIL, so the download workers queued on it (0.3 symbols/s
    with 12 workers on the server).  Tencent's responses are strict JSON, for
    which ``json.loads`` returns the same objects about 300x faster; anything
    it rejects still goes to demjson.
    """

    def __init__(self, fallback: object) -> None:
        self._fallback = fallback

    def decode(self, text: str, *args: object, **kwargs: object) -> object:
        if not args and not kwargs:
            try:
                return json.loads(text)
            except ValueError:
                pass
        return self._fallback.decode(text, *args, **kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._fallback, name)


FAST_JSON_MODULES = ("akshare.stock_feature.stock_hist_tx", "akshare.index.index_stock_zh")


def install_fast_json_decoder() -> None:
    """Point the Tencent history modules (daily bars, index bars, start-year
    lookup) at :class:`_FastDemjson`; idempotent."""
    with _TIMEOUT_LOCK:
        for name in FAST_JSON_MODULES:
            module = importlib.import_module(name)
            current = getattr(module, "demjson")
            if not isinstance(current, _FastDemjson):
                module.demjson = _FastDemjson(current)


class ProviderError(RuntimeError):
    """A vendor call failed; ``terminal`` means retrying cannot help."""

    def __init__(self, message: str, terminal: bool) -> None:
        super().__init__(message)
        self.terminal = terminal


def _is_terminal(exc: Exception) -> bool:
    # Parse errors on a well-formed response are deterministic; transport
    # failures and truncated JSON are worth another attempt.
    if isinstance(exc, (json.JSONDecodeError, requests.RequestException, OSError)):
        return False
    return isinstance(exc, (KeyError, IndexError, TypeError, AttributeError, ValueError))


class AkShareProvider(MarketDataProvider):
    """AKShare adapter with timeouts, classified retries and fixed sources.

    Source choice is deterministic per market so a symbol's history never
    splices vendors with different conventions: SSE/SZSE bars from Tencent,
    BSE bars from Sina, factors from Sina.  (Eastmoney's kline hosts are
    blocked from this deployment; see ADR-003.)
    """

    def __init__(
        self,
        expected_version: str,
        max_retries: int = 4,
        request_pause_seconds: float = 0.35,
        timeout_seconds: float = 30.0,
    ) -> None:
        actual_version = getattr(ak, "__version__", "unknown")
        if actual_version != expected_version:
            raise RuntimeError(
                f"AKShare version mismatch: expected {expected_version}, "
                f"found {actual_version}. Pin the tested version before ingestion."
            )
        self.max_retries = max_retries
        self.request_pause_seconds = request_pause_seconds
        self.timeout_seconds = timeout_seconds
        self.logger = logging.getLogger(self.__class__.__name__)
        self._baidu_cookie: str | None = None
        install_default_timeout(timeout_seconds)
        install_fast_json_decoder()

    @property
    def version(self) -> str:
        return str(ak.__version__)

    def _call(self, name: str, func: Callable[..., T], attempts: int | None = None, **kwargs: object) -> T:
        last_error: Exception | None = None
        max_attempts = attempts or self.max_retries
        for attempt in range(1, max_attempts + 1):
            try:
                result = func(**kwargs)
                if self.request_pause_seconds:
                    time.sleep(self.request_pause_seconds)
                return result
            except Exception as exc:  # upstream errors are heterogeneous
                last_error = exc
                if _is_terminal(exc) or attempt >= max_attempts:
                    break
                delay = min(2 ** (attempt - 1), 8)
                self.logger.warning(
                    "%s failed on attempt %s/%s: %s; retrying in %ss",
                    name, attempt, max_attempts, exc, delay,
                )
                time.sleep(delay)
        terminal = last_error is not None and _is_terminal(last_error)
        raise ProviderError(
            f"AKShare call {name} failed: {type(last_error).__name__}: {last_error}",
            terminal=terminal,
        ) from last_error

    # -- reference data ----------------------------------------------------

    def fetch_trading_calendar(self) -> pd.DataFrame:
        return self._call("tool_trade_date_hist_sina", ak.tool_trade_date_hist_sina)

    def fetch_security_lists(self) -> dict[str, pd.DataFrame]:
        return {
            "sse_main": self._call(
                "stock_info_sh_name_code.main", ak.stock_info_sh_name_code, symbol="主板A股"
            ),
            "sse_star": self._call(
                "stock_info_sh_name_code.star", ak.stock_info_sh_name_code, symbol="科创板"
            ),
            "szse": self._call(
                "stock_info_sz_name_code", ak.stock_info_sz_name_code, symbol="A股列表"
            ),
            "bse": self._call("stock_info_bj_name_code", ak.stock_info_bj_name_code),
            "sse_delisted": self._call(
                "stock_info_sh_delist", ak.stock_info_sh_delist, symbol="全部"
            ),
            "szse_delisted": self._call(
                "stock_info_sz_delist", ak.stock_info_sz_delist, symbol="终止上市公司"
            ),
        }

    # -- bars ----------------------------------------------------------------

    def fetch_daily_bars(
        self, symbol: str, start_date: str, end_date: str, delisted: bool = False
    ) -> FetchResult:
        prefixed = market_prefix(symbol)
        if infer_exchange(symbol) == "BSE":
            # The wrapper's second (share-capital) request fails for delisted
            # codes, so they go straight to the plain history endpoint.  Live
            # codes only fall back on a deterministic failure: the fallback
            # has no share capital, so turnover_rate_pct would be lost.
            if not delisted:
                try:
                    frame = self._call(
                        "stock_zh_a_daily", ak.stock_zh_a_daily,
                        symbol=prefixed, start_date=start_date, end_date=end_date, adjust="",
                    )
                    return FetchResult(frame, "akshare.stock_zh_a_daily.sina")
                except ProviderError as exc:
                    if not exc.terminal:
                        raise
                    self.logger.warning("Sina daily wrapper failed for %s, using raw history: %s", symbol, exc)
            frame = self.fetch_sina_raw_history(symbol)
            if not frame.empty:
                dates = pd.to_datetime(frame["date"])
                frame = frame[
                    (dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))
                ].reset_index(drop=True)
            return FetchResult(frame, "akshare.stock_zh_a_cdr_daily.sina")
        frame = self._call(
            "stock_zh_a_hist_tx", ak.stock_zh_a_hist_tx,
            symbol=prefixed, start_date=start_date, end_date=end_date, adjust="",
            timeout=self.timeout_seconds,
        )
        return FetchResult(frame, "akshare.stock_zh_a_hist_tx.tencent")

    def fetch_sina_raw_history(self, symbol: str) -> pd.DataFrame:
        """Full Sina history without the wrapper's share-capital merge.

        ``stock_zh_a_cdr_daily`` requests only the hist endpoint and keeps the
        ``prevclose`` column, which Sina fills on ex-rights dates only.
        """
        frame = self._call(
            "stock_zh_a_cdr_daily", ak.stock_zh_a_cdr_daily,
            symbol=market_prefix(symbol), start_date="19900101", end_date="21000101",
        )
        return frame.reset_index(drop=True)

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        frame = self._call(
            "stock_zh_a_hist_tx.index", ak.stock_zh_a_hist_tx,
            symbol=symbol, start_date=start_date, end_date=end_date, adjust="",
            timeout=self.timeout_seconds,
        )
        return FetchResult(frame, "akshare.stock_zh_a_hist_tx.tencent")

    # -- adjustment factors --------------------------------------------------

    def fetch_adjustment_factors(self, symbol: str) -> FetchResult:
        """Event-style backward (hfq) factors.

        Sina's qfq factors are redundant (qfq = latest hfq / hfq) and are
        re-anchored at every event, so only hfq is fetched.
        """
        try:
            frame = self._call(
                "stock_zh_a_daily.hfq_factor", ak.stock_zh_a_daily,
                symbol=market_prefix(symbol), adjust="hfq-factor",
            )
            if "date" not in frame.columns or "hfq_factor" not in frame.columns:
                raise ProviderError(
                    f"Unexpected hfq factor columns for {symbol}: {list(frame.columns)}",
                    terminal=True,
                )
            return FetchResult(frame[["date", "hfq_factor"]], "akshare.stock_zh_a_daily.sina")
        except ProviderError as exc:
            if not exc.terminal:
                raise
            self.logger.warning(
                "Sina factors unavailable for %s; deriving from Sina ex-rights prices: %s",
                symbol, exc,
            )
        history = self.fetch_sina_raw_history(symbol)
        return FetchResult(
            derive_hfq_events_from_prevclose(history),
            "akshare.stock_zh_a_cdr_daily.sina.prevclose_derived",
        )

    # -- snapshot ------------------------------------------------------------

    def fetch_market_snapshot(self) -> FetchResult:
        try:
            frame = self._call("stock_zh_a_spot_em", ak.stock_zh_a_spot_em)
            return FetchResult(frame, "akshare.stock_zh_a_spot_em.eastmoney")
        except ProviderError as exc:
            self.logger.warning("Eastmoney snapshot unavailable; using Tencent: %s", exc)
        frame = self._call("stock_zh_a_spot_tx", ak.stock_zh_a_spot_tx)
        return FetchResult(frame, "akshare.stock_zh_a_spot_tx.tencent")

    # -- status history ------------------------------------------------------

    def fetch_suspension_snapshot(self, date: str) -> pd.DataFrame:
        """Eastmoney datacenter: suspensions resuming on or after ``date``."""
        return self._call("stock_tfp_em", ak.stock_tfp_em, date=date)

    def fetch_suspension_events(self, date: str) -> pd.DataFrame:
        """Baidu calendar: suspensions that start on ``date``."""
        if self._baidu_cookie is None:
            self._baidu_cookie = _baidu_cookie()
        return self._call(
            "news_trade_notify_suspend_baidu", ak.news_trade_notify_suspend_baidu,
            date=date, cookie=self._baidu_cookie,
        )

    def fetch_sz_name_changes(self) -> pd.DataFrame:
        return self._call(
            "stock_info_sz_change_name", ak.stock_info_sz_change_name, symbol="简称变更"
        )

    # -- exchange bulletins (risk-warning history) -----------------------------

    def fetch_sse_bulletins(self, title: str, start: str, end: str) -> pd.DataFrame:
        """SSE company bulletins whose title contains ``title`` (YYYY-MM-DD).

        The service returns nothing for windows longer than about a quarter,
        so callers pass quarterly windows.
        """
        rows: list[dict] = []
        page, pages = 1, 1
        while page <= pages:
            time.sleep(SSE_BULLETIN_PAUSE_SECONDS)  # the SSE query service throttles bursts
            # Retrying hard during an anti-crawling block only prolongs it.
            data, total = self._call(f"sse_bulletins:{title}:{start}:{page}", _sse_bulletin_page,
                                     attempts=BULLETIN_ATTEMPTS, title=title, start=start, end=end, page=page)
            rows.extend(data)
            pages = max(1, math.ceil(total / SSE_PAGE_SIZE))
            page += 1
        return pd.DataFrame(rows)

    def fetch_bse_announcements(self, keyword: str, start: str, end: str) -> pd.DataFrame:
        """BSE announcements whose title contains ``keyword`` (YYYY-MM-DD)."""
        rows: list[dict] = []
        page, pages = 0, 1
        while page < pages:
            data, pages = self._call(f"bse_announcements:{keyword}:{start}:{page}", _bse_announcement_page,
                                     attempts=BULLETIN_ATTEMPTS, keyword=keyword, start=start, end=end, page=page)
            rows.extend(data)
            page += 1
        return pd.DataFrame(rows)

    # -- stage 3: corporate data, classification, total-return indices ---------

    def fetch_datacenter(self, report: str, filter: str, sort_columns: str) -> pd.DataFrame:
        """All pages of an Eastmoney datacenter report; pages are retried
        individually and the whole report once if the row count drifts."""
        def page(**kwargs: object) -> tuple[list[dict], int, int]:
            return self._call(f"datacenter:{report}:{filter}:{kwargs['page']}", dc_page, **kwargs)

        for attempt in (1, 2):
            try:
                return collect_pages(page, report, filter, sort_columns, workers=DC_PAGE_WORKERS)
            except PaginationMismatch as exc:
                if attempt == 2:
                    raise ProviderError(str(exc), terminal=False) from exc
                self.logger.warning("%s; refetching the whole report", exc)
        raise AssertionError("unreachable")

    def fetch_csindex_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        # sh000510 is stored with its exchange prefix (000510 alone is also a stock code).
        code = symbol[2:] if symbol[:2] in ("sh", "sz") else symbol
        frame = self._call(f"stock_zh_index_hist_csindex:{symbol}", ak.stock_zh_index_hist_csindex,
                           symbol=code, start_date=start_date, end_date=end_date)
        return FetchResult(frame, SOURCE_CSINDEX)

    def fetch_index_weights(self, symbol: str) -> pd.DataFrame:
        return self._call(f"index_stock_cons_weight_csindex:{symbol}", ak.index_stock_cons_weight_csindex,
                          symbol=symbol)

    def fetch_sw_classification(self) -> dict[str, pd.DataFrame]:
        return {kind: self._call(f"sw_classification:{kind}", sw_file, kind=kind) for kind in ("history", "codes")}

    def fetch_share_capital(self, date_field: str, start: str, end: str) -> pd.DataFrame:
        if date_field not in {"END_DATE", "NOTICE_DATE"}:
            raise ValueError(f"unsupported share-capital date field {date_field}")
        filter = f"({date_field}>='{start}')({date_field}<'{end}'){A_SHARE_TYPES}"
        return self.fetch_datacenter("RPT_F10_EH_EQUITY", filter, "SECUCODE,END_DATE")

    def fetch_financial_statement(self, statement: str, company_type: str, date_field: str, start: str,
                                  end: str) -> pd.DataFrame:
        names = {"income": "INCOME", "balance": "BALANCE", "cashflow": "CASHFLOW"}
        if statement not in names or company_type not in {"G", "B", "S", "I"}:
            raise ValueError(f"unknown statement {statement!r} / company type {company_type!r}")
        if date_field not in {"REPORT_DATE", "UPDATE_DATE"}:
            raise ValueError(f"unsupported financial date field {date_field}")
        filter = f"({date_field}>='{start}')({date_field}<'{end}'){A_SHARE_TYPES}"
        return self.fetch_datacenter(f"RPT_F10_FINANCE_{company_type}{names[statement]}", filter,
                                     "SECUCODE,REPORT_DATE")

    def fetch_earnings_forecast(self, date_field: str, start: str, end: str) -> pd.DataFrame:
        if date_field not in {"REPORT_DATE", "NOTICE_DATE"}:
            raise ValueError(f"unsupported forecast date field {date_field}")
        filter = f"({date_field}>='{start}')({date_field}<'{end}'){A_SHARE_TYPES}"
        return self.fetch_datacenter("RPT_PUBLIC_OP_NEWPREDICT", filter,
                                     "SECURITY_CODE,NOTICE_DATE,PREDICT_FINANCE_CODE")

    def fetch_intraday_bars(self, code: str) -> pd.DataFrame:
        if code[:2] not in ("sh", "sz"):
            return self._call(f"futures_minute:{code}", ak.futures_zh_minute_sina, symbol=code, period="1")
        return self._call(f"sina_minute:{code}", _sina_minutes, code=code)

    def fetch_intraday_trades(self, code: str) -> pd.DataFrame:
        return self._call(f"tencent_ticks:{code}", ak.stock_zh_a_tick_tx_js, symbol=code)

    def fetch_dividends(self, report_date: str) -> pd.DataFrame:
        return self.fetch_datacenter("RPT_SHAREBONUS_DET", f"(REPORT_DATE='{report_date}')", "SECUCODE")

    def fetch_etf_shares_sse(self, trade_date: str) -> pd.DataFrame:
        # Queried directly: AKShare's fund_etf_scale_sse raises on a day without data.
        time.sleep(SSE_BULLETIN_PAUSE_SECONDS)  # the same throttled SSE query service
        return self._call(f"sse_etf_scale:{trade_date}", _sse_etf_scale, trade_date=trade_date)

    def fetch_etf_lists(self) -> dict[str, pd.DataFrame]:
        time.sleep(SSE_BULLETIN_PAUSE_SECONDS)
        return {"sse": self._call("sse_fund_list", _sse_fund_list), "szse": self._call("szse_etf_list", _szse_etf_list)}

    def fetch_fund_reports(self, symbol: str) -> pd.DataFrame:
        return self._call(f"fund_reports:{symbol}", _fund_reports, symbol=symbol)

    def fetch_report_text(self, art_code: str) -> str:
        return self._call(f"fund_report_text:{art_code}", _report_text, art_code=art_code)

    def fetch_etf_shares_szse(self, start: str, end: str) -> pd.DataFrame:
        return self._call(f"fund_scale_daily_szse:{start}", ak.fund_scale_daily_szse,
                          start_date=start, end_date=end, symbol="ETF")


SOURCE_CSINDEX = "akshare.stock_zh_index_hist_csindex.csindex"
SSE_PAGE_SIZE = 100
SSE_BULLETIN_PAUSE_SECONDS = 3.0
BULLETIN_ATTEMPTS = 2
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0 Safari/537.36")


def _jsonp(text: str) -> object:
    return json.loads(text[text.find("(") + 1:text.rfind(")")])


def _sse_bulletin_page(title: str, start: str, end: str, page: int) -> tuple[list[dict], int]:
    params = {
        "jsonCallBack": "jsonpCallback1", "isPagination": "true", "pageHelp.pageSize": str(SSE_PAGE_SIZE),
        "pageHelp.cacheSize": "1", "pageHelp.pageNo": str(page), "pageHelp.beginPage": str(page),
        "pageHelp.endPage": str(page), "START_DATE": start, "END_DATE": end, "SECURITY_CODE": "",
        "TITLE": title, "BULLETIN_TYPE": "", "stockType": "",
    }
    response = requests.get(
        "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do", params=params,
        headers={"Referer": "https://www.sse.com.cn/", "User-Agent": _BROWSER_UA},
    )
    response.raise_for_status()
    payload = _jsonp(response.text)
    help_ = payload["pageHelp"]
    data = [item for row in (help_["data"] or []) for item in (row if isinstance(row, list) else [row])]
    return data, int(help_["total"] or 0)


def _sina_minutes(code: str) -> pd.DataFrame:
    """Sina 1-minute bars: the newest 1,970 (about eight sessions), the most it serves."""
    response = requests.get("https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData",
                            params={"symbol": code, "scale": "1", "ma": "no", "datalen": "1970"},
                            headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": _BROWSER_UA}, timeout=30)
    response.raise_for_status()
    return pd.DataFrame(_jsonp(response.text) or [])


def _sse_etf_scale(trade_date: str) -> pd.DataFrame:
    params = {
        "isPagination": "true", "pageHelp.pageSize": "10000", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1", "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
        "STAT_DATE": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
    }
    response = requests.get("https://query.sse.com.cn/commonQuery.do", params=params,
                            headers={"Referer": "https://www.sse.com.cn/", "User-Agent": _BROWSER_UA})
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("result") or []
    total = int((payload.get("pageHelp") or {}).get("total") or 0)
    if total > len(rows):  # one page of 10,000 holds every fund; more means the query changed
        raise PaginationMismatch(f"SSE ETF scale {trade_date}: {len(rows)} of {total} rows")
    return pd.DataFrame(rows, columns=["STAT_DATE", "ETF_TYPE", "SEC_CODE", "NUM", "SEC_NAME", "TOT_VOL"])


def _sse_fund_list() -> pd.DataFrame:
    params = {
        "isPagination": "true", "pageHelp.pageSize": "5000", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "1", "sqlId": "FUND_LIST", "fundType": "00",
        "subClass": "01,03,02,04,06,08,09,31,32,33,34,35,36,37,38",
    }
    response = requests.get("https://query.sse.com.cn/commonSoaQuery.do", params=params,
                            headers={"Referer": "https://www.sse.com.cn/", "User-Agent": _BROWSER_UA})
    response.raise_for_status()
    help_ = response.json()["pageHelp"]
    rows, total = help_["data"] or [], int(help_["total"] or 0)
    if total > len(rows):
        raise PaginationMismatch(f"SSE fund list: {len(rows)} of {total} rows")
    return pd.DataFrame(rows).astype("string")


def _szse_etf_list() -> pd.DataFrame:
    response = requests.get(
        "https://www.szse.cn/api/report/ShowReport",
        params={"SHOWTYPE": "xlsx", "CATALOGID": "1945", "TABKEY": "tab1", "random": "0.5"},
        headers={"Referer": "https://www.szse.cn/market/product/list/etfList/index.html", "User-Agent": _BROWSER_UA},
    )
    response.raise_for_status()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl: workbook has no default style
        frame = pd.read_excel(io.BytesIO(response.content), engine="openpyxl", dtype=str)
    missing = {"证券代码", "证券简称", "拟合指数", "基金管理人"} - set(frame.columns)
    if missing:
        raise ValueError(f"SZSE ETF list lacks {sorted(missing)}")
    return frame


def _fund_reports(symbol: str) -> pd.DataFrame:
    """Eastmoney fund F10, 定期报告 (type 3), 100 per page."""
    rows: list[dict] = []
    page, total = 1, None
    while total is None or len(rows) < total:
        response = requests.get(
            "http://api.fund.eastmoney.com/f10/JJGG",
            params={"fundcode": symbol, "pageIndex": str(page), "pageSize": "100", "type": "3",
                    "_": str(int(time.time() * 1000))},
            headers={"Referer": f"http://fundf10.eastmoney.com/jjgg_{symbol}_3.html", "User-Agent": _BROWSER_UA},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("Data") or []
        total = int(payload.get("TotalCount") or 0)
        if not data:
            break
        rows.extend(data)
        page += 1
    if total and len(rows) < total:
        raise PaginationMismatch(f"fund reports {symbol}: {len(rows)} of {total}")
    return pd.DataFrame(rows, columns=["FUNDCODE", "TITLE", "PUBLISHDATEDesc", "ID"]).astype("string")


def _report_text(art_code: str) -> str:
    response = requests.get(
        "https://np-cnotice-fund.eastmoney.com/api/content/ann",
        params={"client_source": "web_fund", "show_all": "1", "art_code": art_code},
        headers={"Referer": "https://fund.eastmoney.com/", "User-Agent": _BROWSER_UA},
    )
    response.raise_for_status()
    text = ((response.json() or {}).get("data") or {}).get("notice_content")
    if not text:
        raise ValueError(f"announcement {art_code} has no text")
    return str(text)


def _bse_announcement_page(keyword: str, start: str, end: str, page: int) -> tuple[list[dict], int]:
    form = {
        "disclosureType[]": "5", "page": str(page), "companyCd": "", "isNewThree": "1", "startTime": start,
        "endTime": end, "keyword": keyword, "xxfcbj[]": "2",
        "needFields[]": ["companyCd", "companyName", "disclosureTitle", "destFilePath", "publishDate",
                         "xxfcbj", "fileExt", "xxzrlx"],
    }
    response = requests.post(
        "https://www.bse.cn/disclosureInfoController/companyAnnouncement.do", params={"callback": "cb"},
        data=form, headers={"Referer": "https://www.bse.cn/disclosure/announcement.html", "User-Agent": _BROWSER_UA},
    )
    response.raise_for_status()
    listing = _jsonp(response.text)[0]["listInfo"]
    return list(listing["content"]), int(listing["totalPages"])


def _baidu_cookie() -> str | None:
    """Fetch one Baidu session cookie to reuse across calendar requests.

    Uses AKShare's private helper; on failure each call fetches its own.
    """
    try:
        from akshare.news import news_baidu

        return news_baidu._get_baidu_cookie(
            {
                "accept": "application/vnd.finance-web.v1+json",
                "origin": "https://finance.baidu.com",
                "referer": "https://finance.baidu.com/",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            }
        )
    except Exception:  # noqa: BLE001 - optional optimisation
        return None


def derive_hfq_events_from_prevclose(history: pd.DataFrame) -> pd.DataFrame:
    """Proportional hfq events from Sina's ex-rights reference price.

    On an ex-rights date Sina publishes ``prevclose`` = the exchange's
    ex-rights reference price; the hfq step is prior raw close / reference.
    Returns event rows plus a 1900-01-01 baseline of 1.0.
    """
    required = {"date", "close", "prevclose"}
    if history is None or history.empty or not required.issubset(history.columns):
        raise ProviderError(
            f"Cannot derive factors: history columns {list(getattr(history, 'columns', []))}",
            terminal=True,
        )
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date", ignore_index=True)
    close = pd.to_numeric(frame["close"], errors="coerce")
    reference = pd.to_numeric(frame["prevclose"], errors="coerce")
    previous_close = close.shift(1)
    step = previous_close / reference
    is_event = reference.notna() & (reference > 0) & previous_close.notna() & ((step - 1).abs() > 1e-6)
    events = pd.DataFrame({"date": frame.loc[is_event, "date"], "step": step[is_event]})
    hfq = events["step"].cumprod()
    result = pd.concat(
        [
            pd.DataFrame({"date": [pd.Timestamp("1900-01-01")], "hfq_factor": [1.0]}),
            pd.DataFrame({"date": events["date"].to_numpy(), "hfq_factor": hfq.to_numpy()}),
        ],
        ignore_index=True,
    )
    return result
