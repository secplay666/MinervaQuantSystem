"""In-memory stand-in for the AKShare adapter, shaped like real responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from quant_system.data_platform.config import DataPlatformConfig, IndexConfig, ManualDelisting
from quant_system.data_platform.providers.base import FetchResult, MarketDataProvider
from quant_system.data_platform.sessions import SHANGHAI_TZ
from quant_system.data_platform.symbols import market_prefix

HOLIDAY = date(2026, 9, 25)


def business_days(start: date, end: date) -> list[date]:
    days, current = [], start
    while current <= end:
        if current.weekday() < 5 and current != HOLIDAY:
            days.append(current)
        current += timedelta(days=1)
    return days


OPEN_DATES = business_days(date(2026, 9, 1), date(2026, 12, 31))


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI_TZ)


def make_config(**overrides: object) -> DataPlatformConfig:
    values: dict[str, object] = dict(
        provider="akshare",
        provider_version="fake",
        market="A_SHARE",
        start_date="20260901",
        index_start_date="20260901",
        end_date=None,
        request_pause_seconds=0.0,
        max_retries=1,
        max_workers=2,
        daily_universe="all_a_share",
        download_adjustment_factors=True,
        symbols=(),
        indices=(IndexConfig("sh000001", "上证指数"),),
        suspension_backfill_start="20260901",
        manual_delistings=(
            ManualDelisting("920002", "测试退", "20250101", "20260916", "test"),
        ),
        config_hash="test",
    )
    values.update(overrides)
    return DataPlatformConfig(**values)  # type: ignore[arg-type]


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: int  # shares


def default_bar(symbol: str, day: date) -> Bar:
    index = OPEN_DATES.index(day)
    base = 10.0 + (int(symbol[-2:]) % 7) + index * 0.1
    return Bar(open=round(base - 0.05, 2), high=round(base + 0.1, 2), low=round(base - 0.15, 2),
               close=round(base, 2), volume=100_000 + index)


@dataclass
class FakeProvider(MarketDataProvider):
    """Data for a handful of symbols; tests mutate it between runs."""

    today: date = date(2026, 9, 30)
    listed: dict[str, tuple[str, str]] = field(default_factory=lambda: {
        "600001": ("甲股份", "2010-01-04"),
        "000001": ("乙银行", "1991-04-03"),
        "300001": ("丙科技", "2026-09-10"),
        "920001": ("丁精密", "2022-01-01"),
        "688001": ("己科创", "2019-07-22"),
    })
    delisted_sse: dict[str, tuple[str, str, str]] = field(default_factory=lambda: {
        "600002": ("退市戊", "2001-01-01", "2026-09-18"),
    })
    bars: dict[str, dict[date, Bar]] = field(default_factory=dict)
    factors: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    empty_daily: set[str] = field(default_factory=set)
    empty_index: bool = False
    tfp_rows: list[dict[str, object]] = field(default_factory=list)
    baidu_rows: dict[date, list[dict[str, object]]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for symbol in list(self.listed) + list(self.delisted_sse) + ["920002"]:
            self.bars.setdefault(symbol, {})
            for day in OPEN_DATES:
                if day > self.today:
                    break
                self.bars[symbol][day] = default_bar(symbol, day)
            self.factors.setdefault(symbol, [("1900-01-01", 1.0), ("2015-06-01", 1.5)])
        listing = date(2026, 9, 10)
        self.bars["300001"] = {day: bar for day, bar in self.bars["300001"].items() if day >= listing}
        for symbol, delist in (("600002", date(2026, 9, 18)), ("920002", date(2026, 9, 16))):
            self.bars[symbol] = {day: bar for day, bar in self.bars[symbol].items() if day < delist}

    @property
    def version(self) -> str:
        return "fake"

    # reference data -------------------------------------------------------

    def fetch_trading_calendar(self) -> pd.DataFrame:
        return pd.DataFrame({"trade_date": [d for d in OPEN_DATES]})

    def fetch_security_lists(self) -> dict[str, pd.DataFrame]:
        sse = [(s, n, d) for s, (n, d) in self.listed.items() if s.startswith("6") and not s.startswith("68")]
        star = [(s, n, d) for s, (n, d) in self.listed.items() if s.startswith("68")]
        szse = [(s, n, d) for s, (n, d) in self.listed.items() if s.startswith(("0", "3"))]
        bse = [(s, n, d) for s, (n, d) in self.listed.items() if s.startswith("9")]
        return {
            "sse_main": pd.DataFrame(
                [{"证券代码": s, "证券简称": n, "上市日期": pd.to_datetime(d).date()} for s, n, d in sse]
                + [{"证券代码": "900901", "证券简称": "某B股", "上市日期": date(1995, 1, 1)}]
            ),
            "sse_star": pd.DataFrame(
                [{"证券代码": s, "证券简称": n, "上市日期": pd.to_datetime(d).date()} for s, n, d in star]
            ),
            "szse": pd.DataFrame(
                [{"板块": "创业板" if s.startswith("3") else "主板", "A股代码": s, "A股简称": n,
                  "A股上市日期": d} for s, n, d in szse]
            ),
            "bse": pd.DataFrame(
                [{"证券代码": s, "证券简称": n, "上市日期": pd.to_datetime(d).date()} for s, n, d in bse]
            ),
            "sse_delisted": pd.DataFrame(
                [{"公司代码": s, "公司简称": n, "上市日期": pd.to_datetime(ld).date(),
                  "暂停上市日期": pd.to_datetime(dd).date()} for s, (n, ld, dd) in self.delisted_sse.items()]
            ),
            # A delisting long before the ingestion window: listed, never fetched.
            "szse_delisted": pd.DataFrame(
                [{"证券代码": "000003", "证券简称": "PT金田Ａ", "上市日期": date(1991, 1, 14),
                  "终止上市日期": date(2002, 6, 14)}]
            ),
        }

    # bars -----------------------------------------------------------------

    def _rows(self, symbol: str, start_date: str, end_date: str) -> list[tuple[date, Bar]]:
        start, end = pd.to_datetime(start_date).date(), pd.to_datetime(end_date).date()
        return [(d, b) for d, b in sorted(self.bars.get(symbol, {}).items()) if start <= d <= end]

    def fetch_daily_bars(
        self, symbol: str, start_date: str, end_date: str, delisted: bool = False
    ) -> FetchResult:
        self.calls.append(("daily_delisted" if delisted else "daily", symbol))
        if symbol in self.empty_daily:
            return FetchResult(pd.DataFrame(), "akshare.stock_zh_a_hist_tx.tencent")
        rows = self._rows(symbol, start_date, end_date)
        if symbol.startswith("9"):
            frame = pd.DataFrame(
                [{"date": d, "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                  "volume": float(b.volume), "amount": b.volume * b.close, "outstanding_share": 1e8,
                  "turnover": b.volume / 1e8} for d, b in rows]
            )
            return FetchResult(frame, "akshare.stock_zh_a_daily.sina")
        # Reproduce AKShare 1.18.78's unit bug: sz000 stays in lots.
        scale = 0.01 if market_prefix(symbol).startswith("sz000") else 1.0
        frame = pd.DataFrame(
            [{"date": d, "open": b.open, "close": b.close, "high": b.high, "low": b.low,
              "volume": b.volume * scale, "turnover": 0.001, "amount": b.volume * b.close} for d, b in rows]
        )
        return FetchResult(frame, "akshare.stock_zh_a_hist_tx.tencent")

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        self.calls.append(("index", symbol))
        if self.empty_index:
            return FetchResult(pd.DataFrame(), "akshare.stock_zh_a_hist_tx.tencent")
        start, end = pd.to_datetime(start_date).date(), pd.to_datetime(end_date).date()
        days = [d for d in OPEN_DATES if start <= d <= min(end, self.today)]
        frame = pd.DataFrame(
            [{"date": d, "open": 3000.0, "close": 3001.0 + i, "high": 3010.0 + i, "low": 2990.0,
              "volume": 3e8, "turnover": 0.01, "amount": 4e11} for i, d in enumerate(days)]
        )
        return FetchResult(frame, "akshare.stock_zh_a_hist_tx.tencent")

    def fetch_adjustment_factors(self, symbol: str) -> FetchResult:
        self.calls.append(("factors", symbol))
        rows = list(reversed(self.factors[symbol]))  # Sina returns newest first
        frame = pd.DataFrame({"date": pd.to_datetime([d for d, _ in rows]), "hfq_factor": [f for _, f in rows]})
        return FetchResult(frame, "akshare.stock_zh_a_daily.sina")

    def fetch_market_snapshot(self) -> FetchResult:
        frame = pd.DataFrame(
            [{"code": market_prefix(s), "name": n, "zxj": 10.0, "volume": 1000.0, "turnover": 100.0}
             for s, (n, _) in self.listed.items()]
        )
        return FetchResult(frame, "akshare.stock_zh_a_spot_tx.tencent")

    def fetch_suspension_snapshot(self, date: str) -> pd.DataFrame:
        columns = ["序号", "代码", "名称", "停牌时间", "停牌截止时间", "停牌期限", "停牌原因", "所属市场", "预计复牌时间"]
        return pd.DataFrame(self.tfp_rows, columns=columns)

    def fetch_suspension_events(self, date: str) -> pd.DataFrame:
        columns = ["股票代码", "股票简称", "交易所代码", "停牌时间", "复牌时间", "停牌事项说明",
                   "市值", "公告日期", "公告时间", "证券类型", "市场类型", "是否跳过"]
        return pd.DataFrame(self.baidu_rows.get(pd.to_datetime(date).date(), []), columns=columns)

    sse_bulletins: list[dict[str, object]] = field(default_factory=list)
    bse_announcements: list[dict[str, object]] = field(default_factory=list)

    def fetch_sse_bulletins(self, title: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append(("sse_bulletins", f"{title}:{start}"))
        rows = [r for r in self.sse_bulletins if title in r["TITLE"] and start <= r["SSEDATE"] <= end]
        return pd.DataFrame(rows, columns=["SECURITY_CODE", "SECURITY_NAME", "SSEDATE", "TITLE", "ORG_BULLETIN_ID"])

    def fetch_bse_announcements(self, keyword: str, start: str, end: str) -> pd.DataFrame:
        rows = [r for r in self.bse_announcements if keyword in r["disclosureTitle"] and start <= r["publishDate"] <= end]
        return pd.DataFrame(rows, columns=["companyCd", "companyName", "disclosureTitle", "destFilePath", "publishDate"])

    def fetch_sz_name_changes(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"变更日期": "2026-09-08", "证券代码": "000001", "证券简称": "乙银行",
              "变更前简称": "ST乙行", "变更后简称": "乙银行"}]
        )
