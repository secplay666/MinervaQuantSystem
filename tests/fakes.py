"""In-memory stand-in for the AKShare adapter, shaped like real responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quant_system.data_platform.config import DataPlatformConfig, IndexConfig, ManualDelisting
from quant_system.data_platform.providers.base import FetchResult, MarketDataProvider
from quant_system.data_platform.sessions import SHANGHAI_TZ
from quant_system.data_platform.symbols import market_prefix

HOLIDAY = date(2026, 9, 25)
REPO_ROOT = Path(__file__).resolve().parents[1]
SW_MAPPING = REPO_ROOT / "configs" / "industry" / "sw2014_to_sw2021_l1.json"
ETF_GROUPS = REPO_ROOT / "configs" / "etf" / "broad_groups.json"


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
        sw_mapping_path=str(SW_MAPPING),
        index_weight_symbols=("000300",),
        etf_sse_start="20260901",
        etf_groups_path=str(ETF_GROUPS),
        etf_szse_start="20260801",
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
    baidu_bare: set[date] = field(default_factory=set)  # days answered with a bare DataFrame() (throttling)
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
        if pd.to_datetime(date).date() in self.baidu_bare:
            return pd.DataFrame()
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

    # stage 3: corporate data, classification, total-return indices -----------

    share_rows: list[dict[str, object]] = field(default_factory=lambda: [
        {"SECUCODE": "600001.SH", "SECURITY_CODE": "600001", "END_DATE": "2010-01-04 00:00:00",
         "NOTICE_DATE": "2009-12-28 00:00:00", "TOTAL_SHARES": 1.0e9, "LISTED_A_SHARES": 6.0e8,
         "LIMITED_A_SHARES": 4.0e8, "CHANGE_REASON": "首发A股上市"},
        {"SECUCODE": "600001.SH", "SECURITY_CODE": "600001", "END_DATE": "2026-09-15 00:00:00",
         "NOTICE_DATE": "2026-09-10 00:00:00", "TOTAL_SHARES": 1.2e9, "LISTED_A_SHARES": 1.2e9,
         "LIMITED_A_SHARES": 0.0, "CHANGE_REASON": "送股上市"},
        {"SECUCODE": "000001.SZ", "SECURITY_CODE": "000001", "END_DATE": "1991-04-03 00:00:00",
         "NOTICE_DATE": "1991-04-01 00:00:00", "TOTAL_SHARES": 2.0e10, "LISTED_A_SHARES": 2.0e10,
         "LIMITED_A_SHARES": 0.0, "CHANGE_REASON": "首发A股上市"},
        {"SECUCODE": "830001.NQ", "SECURITY_CODE": "830001", "END_DATE": "2026-09-01 00:00:00",
         "NOTICE_DATE": "2026-08-30 00:00:00", "TOTAL_SHARES": 5.0e7, "LISTED_A_SHARES": 5.0e7,
         "LIMITED_A_SHARES": 0.0, "CHANGE_REASON": "新三板"},
    ])
    dividend_rows: list[dict[str, object]] = field(default_factory=lambda: [
        {"SECUCODE": "600001.SH", "SECURITY_CODE": "600001", "REPORT_DATE": "2025-12-31 00:00:00",
         "PLAN_NOTICE_DATE": "2026-03-20 00:00:00", "NOTICE_DATE": "2026-06-10 00:00:00",
         "EQUITY_RECORD_DATE": "2026-06-16 00:00:00", "EX_DIVIDEND_DATE": "2026-06-17 00:00:00",
         "PRETAX_BONUS_RMB": 3.0, "BONUS_RATIO": 2.0, "IT_RATIO": None, "TOTAL_SHARES": 1.0e9,
         "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "10送2股派3元(含税)"},
    ])
    sw_history_rows: list[tuple[str, str, str]] = field(default_factory=lambda: [
        ("600001", "2010-01-04 00:00:00", "340301"), ("600001", "2021-07-30 00:00:00", "340501"),
        ("000001", "2014-02-21 00:00:00", "480101"), ("000001", "2021-07-30 00:00:00", "480301"),
        ("688001", "2021-07-30 00:00:00", "270101"), ("300001", "2026-09-10 00:00:00", "270101"),
    ])
    index_weights_date: str = "2026-08-31"
    fail_sw: bool = False

    def fetch_csindex_daily(self, symbol: str, start_date: str, end_date: str) -> FetchResult:
        self.calls.append(("csindex", symbol))
        start, end = pd.to_datetime(start_date).date(), pd.to_datetime(end_date).date()
        days = [d for d in OPEN_DATES if start <= d <= min(end, self.today)]
        # Like the real service: a base-value placeholder dated 1990-01-01 comes first.
        frame = pd.DataFrame(
            [{"日期": "1990-01-01", "指数代码": symbol, "开盘": float("nan"), "最高": float("nan"),
              "最低": float("nan"), "收盘": 1000.0, "成交量": 0.0, "成交金额": 0.0}]
            + [{"日期": d.isoformat(), "指数代码": symbol, "开盘": float("nan"), "最高": float("nan"),
                "最低": float("nan"), "收盘": 6000.0 + i, "成交量": 2.0e10, "成交金额": 5000.0}
               for i, d in enumerate(days)]
        )
        return FetchResult(frame, "akshare.stock_zh_index_hist_csindex.csindex")

    def fetch_index_weights(self, symbol: str) -> pd.DataFrame:
        self.calls.append(("index_weights", symbol))
        rows = [("600001", "甲股份", "上海证券交易所", 60.0), ("000001", "乙银行", "深圳证券交易所", 40.0)]
        return pd.DataFrame([{"日期": self.index_weights_date, "指数代码": symbol, "成分券代码": s,
                              "成分券名称": n, "交易所": x, "权重": w} for s, n, x, w in rows])

    def fetch_sw_classification(self) -> dict[str, pd.DataFrame]:
        self.calls.append(("sw", "classification"))
        if self.fail_sw:
            raise RuntimeError("swsresearch timed out")
        history = pd.DataFrame([{"股票代码": s, "计入日期": d, "行业代码": c, "更新日期": "2025-01-01 00:00:00"}
                                for s, d, c in self.sw_history_rows])
        codes = pd.DataFrame([
            {"行业代码": code, "一级行业名称": l1, "二级行业名称": l2, "三级行业名称": l3}
            for code, l1, l2, l3 in (
                ("340000", "食品饮料", None, None), ("340500", "食品饮料", "白酒Ⅱ", None),
                ("340501", "食品饮料", "白酒Ⅱ", "白酒Ⅲ"), ("480000", "银行", None, None),
                ("480300", "银行", "股份制银行Ⅱ", None), ("480301", "银行", "股份制银行Ⅱ", "股份制银行Ⅲ"),
                ("270000", "电子", None, None), ("270100", "电子", "半导体", None),
                ("270101", "电子", "半导体", "分立器件"),
            )
        ])
        return {"history": history, "codes": codes}

    def fetch_share_capital(self, date_field: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append(("share_capital", f"{date_field}:{start}"))
        rows = [r for r in self.share_rows if start <= str(r[date_field])[:10] < end]
        return pd.DataFrame(rows)

    def fetch_dividends(self, report_date: str) -> pd.DataFrame:
        self.calls.append(("dividends", report_date))
        return pd.DataFrame([r for r in self.dividend_rows if str(r["REPORT_DATE"])[:10] == report_date])

    # ETF shares: two SSE funds and one SZSE fund, 1e8 shares plus 1e6 a day
    etf_failing: set[str] = field(default_factory=set)  # "sse:YYYYMMDD" / "szse:YYYYMM" raise
    etf_unpublished: set[date] = field(default_factory=set)  # days whose table is not out yet

    def _etf_days(self, start: date, end: date) -> list[date]:
        return [d for d in OPEN_DATES if start <= d <= min(end, self.today) and d not in self.etf_unpublished]

    def fetch_etf_shares_sse(self, trade_date: str) -> pd.DataFrame:
        self.calls.append(("etf_sse", trade_date))
        if f"sse:{trade_date}" in self.etf_failing:
            raise RuntimeError("SSE ETF scale unavailable")
        day = pd.to_datetime(trade_date).date()
        rows = [{"STAT_DATE": day.isoformat(), "ETF_TYPE": "单市场ETF", "SEC_CODE": code, "NUM": str(i + 1),
                 "SEC_NAME": name, "TOT_VOL": f"{(1e8 + 1e6 * OPEN_DATES.index(day)) / 1e4:.2f}"}
                for i, (code, name) in enumerate((("510300", "300ETF"), ("510050", "50ETF")))
                if day in self._etf_days(day, day)]
        return pd.DataFrame(rows, columns=["STAT_DATE", "ETF_TYPE", "SEC_CODE", "NUM", "SEC_NAME", "TOT_VOL"])

    # Lists: two 沪深300 funds and one 上证50 fund are plain; 561990 is enhanced; 512880 is not broad.
    etf_lists_failing: bool = False
    etf_sse_list: list[dict[str, str]] = field(default_factory=lambda: [
        {"fundCode": "510300", "fundAbbr": "300ETF", "secNameFull": "沪深300ETF华泰柏瑞", "INDEX_CODE": "000300",
         "INDEX_NAME": "沪深300指数", "companyName": "华泰柏瑞基金", "listingDate": "20120528"},
        {"fundCode": "510050", "fundAbbr": "50ETF", "secNameFull": "上证50ETF华夏", "INDEX_CODE": "000016",
         "INDEX_NAME": "上证50指数", "companyName": "华夏基金", "listingDate": "20050223"},
        {"fundCode": "561990", "fundAbbr": "300增强", "secNameFull": "沪深300增强ETF", "INDEX_CODE": "000300",
         "INDEX_NAME": "沪深300指数", "companyName": "某基金", "listingDate": "20230101"},
        {"fundCode": "512880", "fundAbbr": "证券ETF", "secNameFull": "证券ETF国泰", "INDEX_CODE": "399975",
         "INDEX_NAME": "证券公司指数", "companyName": "国泰基金", "listingDate": "20160808"},
    ])
    etf_szse_list: list[dict[str, str]] = field(default_factory=lambda: [
        {"证券代码": "159919", "证券简称": "沪深300ETF嘉实", "拟合指数": "399300 沪深300", "基金管理人": "嘉实基金"},
        {"证券代码": "159920", "证券简称": "恒生ETF华夏", "拟合指数": "HSI", "基金管理人": "华夏基金"},
    ])

    # Periodic reports: 510300's 2025 annual report lists 中央汇金 twice; its 2026 interim has no table.
    fund_reports: dict[str, list[dict[str, str]]] = field(default_factory=lambda: {
        "510300": [
            {"FUNDCODE": "510300", "TITLE": "某沪深300ETF2026年中期报告", "PUBLISHDATEDesc": "2026-08-29",
             "ID": "AN2026H1"},
            {"FUNDCODE": "510300", "TITLE": "某沪深300ETF2025年年度报告摘要", "PUBLISHDATEDesc": "2026-03-31",
             "ID": "AN2025Y-S"},
            {"FUNDCODE": "510300", "TITLE": "某沪深300ETF2025年年度报告", "PUBLISHDATEDesc": "2026-03-31",
             "ID": "AN2025Y"},
            {"FUNDCODE": "510300", "TITLE": "某沪深300ETF2014年年度报告", "PUBLISHDATEDesc": "2015-03-31",
             "ID": "AN2014Y"},
        ],
    })
    report_texts: dict[str, str] = field(default_factory=lambda: {
        "AN2026H1": "§8 基金份额持有人信息\n8.1 期末基金份额持有人户数及持有人结构\n",
        "AN2025Y": (
            "9.2 期末上市基金前十名持有人 ...... 75\n\n正文\n9.2 期末上市基金前十名持有人\n\n"
            "    序号          持有人名称      持有份额（份）      占上市总份额比例（%）\n\n"
            "      1        中央汇金资产管理有  37,858,474,974.00                        42.62\n"
            "                  限责任公司\n\n"
            "      2        中央汇金投资有限责  35,654,598,859.00                        40.14\n"
            "                    任公司\n\n"
            "              北京诚旸投资有限公\n\n"
            "      3        司－诚旸灵活配置私    179,399,890.00                        0.20\n"
            "                募证券投资基金\n\n"
            "      4        滕伟                  119,018,700.00                        0.13%\n\n"
            "注：前十名持有人为除本基金的联接基金之外的前十名持有人。\n"
            "9.3 期末基金管理人的从业人员持有本基金的情况\n"
        ),
    })
    reports_failing: set[str] = field(default_factory=set)

    def fetch_fund_reports(self, symbol: str) -> pd.DataFrame:
        self.calls.append(("fund_reports", symbol))
        if symbol in self.reports_failing:
            raise RuntimeError("report list unavailable")
        return pd.DataFrame(self.fund_reports.get(symbol, []),
                            columns=["FUNDCODE", "TITLE", "PUBLISHDATEDesc", "ID"]).astype("string")

    def fetch_report_text(self, art_code: str) -> str:
        self.calls.append(("report_text", art_code))
        return self.report_texts[art_code]

    def fetch_etf_lists(self) -> dict[str, pd.DataFrame]:
        self.calls.append(("etf_lists", ""))
        if self.etf_lists_failing:
            raise RuntimeError("fund lists unavailable")
        return {"sse": pd.DataFrame(self.etf_sse_list).astype("string"),
                "szse": pd.DataFrame(self.etf_szse_list).astype(str)}

    def fetch_etf_shares_szse(self, start: str, end: str) -> pd.DataFrame:
        self.calls.append(("etf_szse", start))
        if f"szse:{start[:6]}" in self.etf_failing:
            raise RuntimeError("SZSE report unavailable")
        days = self._etf_days(pd.to_datetime(start).date(), pd.to_datetime(end).date())
        return pd.DataFrame([{"日期": d, "基金代码": "159919", "基金简称": "沪深300ETF嘉实",
                              "基金份额": 1e8 + 1e6 * OPEN_DATES.index(d)} for d in days],
                            columns=["日期", "基金代码", "基金简称", "基金份额"])

    fin_periods: tuple[str, ...] = ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30")
    fin_revision: bool = False  # restate 600001's 2025 annual report (new UPDATE_DATE)

    def _financial_rows(self, statement: str, company_type: str) -> list[dict[str, object]]:
        notice = {"2025-06-30": "2025-08-20", "2025-09-30": "2025-10-28", "2025-12-31": "2026-03-25",
                  "2026-03-31": "2026-04-25", "2026-06-30": "2026-08-25"}
        companies = {"G": [("600001", "SH", 1.0)], "B": [("000001", "SZ", 50.0)], "S": [], "I": []}
        rows = []
        for symbol, suffix, scale in companies[company_type]:
            for period in self.fin_periods:
                quarter = int(period[5:7]) // 3
                revised = self.fin_revision and symbol == "600001" and period == "2025-12-31"
                ytd = scale * 1e8 * quarter * (1.1 if period >= "2026" else 1.0) * (1.05 if revised else 1.0)
                row: dict[str, object] = {
                    "SECUCODE": f"{symbol}.{suffix}", "SECURITY_CODE": symbol, "REPORT_DATE": f"{period} 00:00:00",
                    "REPORT_TYPE": "年报" if quarter == 4 else "季报", "NOTICE_DATE": f"{notice[period]} 00:00:00",
                    "UPDATE_DATE": "2026-09-20 00:00:00" if revised else f"{notice[period]} 00:00:00",
                    "SECURITY_TYPE_CODE": "058001001",
                }
                if statement == "income":
                    row.update({"OPERATE_INCOME": ytd, "PARENT_NETPROFIT": 0.1 * ytd,
                                "DEDUCT_PARENT_NETPROFIT": 0.09 * ytd, "NETPROFIT": 0.11 * ytd})
                    if company_type == "G":
                        row.update({"TOTAL_OPERATE_INCOME": ytd, "OPERATE_COST": 0.6 * ytd})
                elif statement == "balance":
                    row.update({"TOTAL_ASSETS": scale * 2e9, "TOTAL_LIABILITIES": scale * 1.2e9,
                                "TOTAL_EQUITY": scale * 0.8e9, "TOTAL_PARENT_EQUITY": scale * 0.75e9,
                                "SHARE_CAPITAL": 1e9})
                else:
                    row.update({"NETCASH_OPERATE": 0.12 * ytd, "CONSTRUCT_LONG_ASSET": 0.05 * ytd})
                rows.append(row)
        # A NEEQ company and a non-quarter-end period are filtered out downstream.
        rows.append({"SECUCODE": "830001.NQ", "SECURITY_CODE": "830001", "REPORT_DATE": "2025-12-31 00:00:00",
                     "NOTICE_DATE": "2026-03-01 00:00:00", "UPDATE_DATE": "2026-03-01 00:00:00"})
        return rows

    def fetch_financial_statement(self, statement: str, company_type: str, date_field: str, start: str,
                                  end: str) -> pd.DataFrame:
        self.calls.append(("financials", f"{statement}:{company_type}:{date_field}:{start}"))
        column = {"REPORT_DATE": "REPORT_DATE", "UPDATE_DATE": "UPDATE_DATE"}[date_field]
        rows = [r for r in self._financial_rows(statement, company_type) if start <= str(r[column])[:10] < end]
        return pd.DataFrame(rows)

    def fetch_sz_name_changes(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"变更日期": "2026-09-08", "证券代码": "000001", "证券简称": "乙银行",
              "变更前简称": "ST乙行", "变更后简称": "乙银行"}]
        )
