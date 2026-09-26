from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fakes import OPEN_DATES, at
from quant_system.data_platform.normalization import (
    clip_bars,
    drop_invalid_price_rows,
    merge_daily_bars,
    merge_suspension_events,
    normalize_adjustment_factors,
    normalize_baidu_suspensions,
    normalize_daily_bars,
    normalize_index_bars,
    normalize_security_master,
    normalize_sz_name_changes,
    normalize_tfp_suspensions,
    risk_intervals_from_names,
    tencent_volume_scale,
)
from quant_system.data_platform.sessions import latest_final_session, session_offset, snapshot_session
from quant_system.data_platform.symbols import (
    infer_board,
    infer_exchange,
    market_prefix,
    risk_status_from_name,
)


# symbols -------------------------------------------------------------------


def test_exchange_and_prefix_by_code_range() -> None:
    assert infer_exchange("600519") == "SSE"
    assert infer_exchange("300750") == "SZSE"
    assert infer_exchange("920002") == "BSE"
    assert infer_exchange("900901") == "SSE"  # SSE B share, not BSE
    assert market_prefix("900901") == "sh900901"
    assert market_prefix("200002") == "sz200002"
    assert market_prefix("920118") == "bj920118"
    assert market_prefix("sh000300") == "sh000300"
    assert infer_board("689009") == "STAR"
    assert infer_board("302132") == "CHINEXT"


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("*ST天润", "*ST"),
        ("ST 华闻", "ST"),
        ("S*ST佳通", "*ST"),
        ("SST前锋", "ST"),
        ("ＳＴ易联众", "ST"),  # full-width letters
        ("XD*ST海投", "*ST"),
        ("云创退", "DELISTING"),
        ("退市观典", "DELISTING"),
        ("N云创", None),
        ("TCL科技", None),
        ("贵州茅台", None),
    ],
)
def test_risk_status_from_name(name: str, status: str | None) -> None:
    assert risk_status_from_name(name) == status


# sessions ------------------------------------------------------------------


def test_latest_final_session_respects_cutoff_and_holidays() -> None:
    thursday, monday = date(2026, 9, 24), date(2026, 9, 28)
    assert latest_final_session(OPEN_DATES, at(thursday, 15, 59)) == date(2026, 9, 23)
    assert latest_final_session(OPEN_DATES, at(thursday, 16, 0)) == thursday
    # 2026-09-25 is a holiday: Friday and the weekend still point at Thursday.
    assert latest_final_session(OPEN_DATES, at(date(2026, 9, 25), 20)) == thursday
    assert latest_final_session(OPEN_DATES, at(monday, 8)) == thursday
    assert latest_final_session(OPEN_DATES, at(monday, 17), explicit_end=date(2026, 9, 22)) == date(2026, 9, 22)


def test_snapshot_session_labels_by_fetch_time() -> None:
    monday = date(2026, 9, 28)
    assert snapshot_session(OPEN_DATES, at(monday, 10)) == (None, "intraday")
    assert snapshot_session(OPEN_DATES, at(monday, 8)) == (date(2026, 9, 24), "post_close")
    assert snapshot_session(OPEN_DATES, at(monday, 18)) == (monday, "post_close")


def test_session_offset_counts_sessions_not_days() -> None:
    assert session_offset(OPEN_DATES, date(2026, 9, 28), 1) == date(2026, 9, 24)
    assert session_offset(OPEN_DATES, date(2026, 9, 27), 0) == date(2026, 9, 24)


# daily bars ------------------------------------------------------------------


def _tencent_frame(volume: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-09-23", "open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9,
             "volume": volume, "turnover": 0.01, "amount": 1_050_000.0},
            {"date": "2026-09-24", "open": 10.5, "close": 11.0, "high": 11.2, "low": 10.4,
             "volume": volume, "turnover": 0.01, "amount": 1_100_000.0},
        ]
    )


def test_tencent_volume_is_repaired_for_known_akshare_prefix_bug() -> None:
    assert tencent_volume_scale("000001") == 100.0
    assert tencent_volume_scale("689009") == 0.01
    assert tencent_volume_scale("600519") == 1.0
    assert tencent_volume_scale("001872") == 1.0
    frame = normalize_daily_bars(_tencent_frame(1_000), "000001", "run", "t",
                                 source="akshare.stock_zh_a_hist_tx.tencent")
    assert frame["volume_shares"].tolist() == [100_000, 100_000]
    assert frame["turnover_rate_pct"].iloc[0] == pytest.approx(1.0)


def test_eastmoney_lots_convert_to_shares() -> None:
    raw = pd.DataFrame(
        [{"日期": "2026-07-24", "股票代码": "600519", "开盘": 1400.0, "收盘": 1410.0, "最高": 1420.0,
          "最低": 1390.0, "成交量": 123, "成交额": 17_000_000.0, "振幅": 2.1, "涨跌幅": 0.7,
          "涨跌额": 10.0, "换手率": 0.2}]
    )
    result = normalize_daily_bars(raw, "600519", "run", "2026-07-25T00:00:00Z")
    assert result.loc[0, "volume_shares"] == 12_300
    assert result.loc[0, "price_adjustment"] == "NONE"


def test_derived_fields_do_not_depend_on_merge_mode() -> None:
    source = "akshare.stock_zh_a_hist_tx.tencent"
    full = normalize_daily_bars(_tencent_frame(1_000), "600001", "r1", "t", source)
    first = normalize_daily_bars(_tencent_frame(1_000).iloc[:1], "600001", "r1", "t", source)
    second = normalize_daily_bars(_tencent_frame(1_000).iloc[1:], "600001", "r2", "t", source)
    incremental = merge_daily_bars(first, second)
    columns = ["trade_date", "pct_change", "change_cny", "amplitude_pct"]
    pd.testing.assert_frame_equal(full[columns], incremental[columns])
    assert pd.isna(full["pct_change"].iloc[0])
    assert full["pct_change"].iloc[1] == pytest.approx(11.0 / 10.5 * 100 - 100)


def test_zero_price_rows_are_dropped_and_counted() -> None:
    frame = normalize_daily_bars(_tencent_frame(1_000), "600001", "r", "t", "akshare.stock_zh_a_hist_tx.tencent")
    frame.loc[0, ["open", "high", "low", "close"]] = 0.0
    kept, dropped = drop_invalid_price_rows(frame)
    assert dropped == 1 and len(kept) == 1


def test_clip_bars_to_listing_window() -> None:
    frame = normalize_daily_bars(_tencent_frame(1_000), "600001", "r", "t", "akshare.stock_zh_a_hist_tx.tencent")
    kept, clipped = clip_bars(frame, date(2026, 9, 24), None)
    assert clipped == 1 and kept["trade_date"].tolist() == [date(2026, 9, 24)]


def test_tencent_index_volume_lots_to_shares() -> None:
    raw = pd.DataFrame([{"date": "2026-09-24", "open": 1.0, "close": 1.0, "high": 1.0, "low": 1.0,
                         "volume": 5.0, "turnover": 0.0, "amount": 10.0}])
    frame = normalize_index_bars(raw, "sh000300", "沪深300", "20200101", "20261231", "r", "t")
    assert frame["volume_shares"].iloc[0] == 500


# factors -----------------------------------------------------------------------


def test_factors_use_one_multiplier_convention() -> None:
    raw = pd.DataFrame({"date": ["2026-06-26", "2015-01-01", "1900-01-01"], "hfq_factor": ["8.0", "2.0", "1.0"]})
    frame = normalize_adjustment_factors(raw, "600519", "run", "t", factor_as_of=date(2026, 9, 24))
    assert frame["effective_date"].tolist() == [date(1900, 1, 1), date(2015, 1, 1), date(2026, 6, 26)]
    assert frame["qfq_factor"].tolist() == [0.125, 0.25, 1.0]
    assert set(frame["factor_as_of"]) == {date(2026, 9, 24)}


# security master --------------------------------------------------------------------


def test_security_master_merges_listed_delisted_and_manual() -> None:
    lists = {
        "sse_main": pd.DataFrame([
            {"证券代码": "600001", "证券简称": "甲", "上市日期": date(2000, 1, 1)},
            {"证券代码": "900901", "证券简称": "某B股", "上市日期": date(1995, 1, 1)},
        ]),
        "szse": pd.DataFrame([
            {"板块": "创业板", "A股代码": "300001", "A股简称": "乙  科Ａ", "A股上市日期": "2010-01-01"},
            {"板块": "主板", "A股代码": "000004", "A股简称": "国华退", "A股上市日期": "1990-12-01"},
        ]),
        "sse_delisted": pd.DataFrame([
            {"公司代码": "600002", "公司简称": "退市甲", "上市日期": date(1999, 1, 1), "暂停上市日期": date(2024, 1, 2)},
            {"公司代码": "600003", "公司简称": "旧股", "上市日期": date(1999, 1, 1), "暂停上市日期": date(2010, 1, 1)},
            {"公司代码": "600001", "公司简称": "同码旧股", "上市日期": date(1993, 1, 1), "暂停上市日期": date(1998, 1, 1)},
        ]),
    }
    manual = [{"symbol": "920305", "name": "云创退", "list_date": "20210826", "delist_date": "20260730",
               "source": "test"}]
    master = normalize_security_master(lists, manual, "run", "t").set_index("symbol")
    assert "900901" not in master.index
    assert master.loc["600001", "status"] == "listed"  # live code wins over the old delisted user
    assert master.loc["300001", "name"] == "乙科A"
    assert master.loc["300001", "list_date"] == date(2010, 1, 1)
    assert master.loc["000004", "status"] == "delisting_period"
    assert master.loc["600002", "delist_date"] == date(2024, 1, 2)
    assert master.loc["920305", "board"] == "BSE"
    assert master.loc["920305", "list_date"] == date(2021, 8, 26)
    assert pd.isna(master.loc["600001", "delist_date"])


# suspensions and names -------------------------------------------------------------------


def test_suspension_sources_normalize_and_merge_append_only() -> None:
    tfp = pd.DataFrame([{"序号": 0, "代码": "300333", "名称": "兆日科技", "停牌时间": "2026-07-30",
                         "停牌截止时间": "2026-08-12", "停牌期限": "连续停牌", "停牌原因": "刊登重要公告",
                         "所属市场": "深交所创业板", "预计复牌时间": "2026-08-13"},
                        {"序号": 1, "代码": "920229", "名称": "世纪数码", "停牌时间": "2026-09-22",
                         "停牌截止时间": "2026-09-22", "停牌期限": "盘中停牌", "停牌原因": "交易异常波动",
                         "所属市场": "北京证券交易所", "预计复牌时间": None}])
    baidu = pd.DataFrame([{"股票代码": "000793", "股票简称": "ST华闻", "交易所代码": "SZ", "停牌时间": "2026-07-30",
                           "复牌时间": "2026-07-31", "停牌事项说明": "撤销退市风险警示"},
                          {"股票代码": "02535", "股票简称": "港股", "交易所代码": "HK", "停牌时间": "2026-07-30",
                           "复牌时间": None, "停牌事项说明": "短暂停止买卖"}])
    first = normalize_tfp_suspensions(tfp, date(2026, 8, 1), "r1", "t")
    assert first.set_index("symbol").loc["920229", "suspension_type"] == "intraday"
    events = normalize_baidu_suspensions(baidu, date(2026, 7, 30), "r1", "t")
    assert events["symbol"].tolist() == ["000793"]
    merged = merge_suspension_events(first, events)
    later = normalize_tfp_suspensions(tfp.iloc[:1], date(2026, 8, 5), "r2", "t")
    merged = merge_suspension_events(merged, later)
    assert len(merged) == 3  # the re-observed event replaces itself, nothing is lost
    assert merged.set_index("symbol").loc["300333", "run_id"] == "r2"


def test_name_changes_to_intervals() -> None:
    raw = pd.DataFrame([
        {"变更日期": "2020-05-06", "证券代码": "000100", "证券简称": "x", "变更前简称": "甲公司", "变更后简称": "*ST甲"},
        {"变更日期": "2021-05-06", "证券代码": "000100", "证券简称": "x", "变更前简称": "*ST甲", "变更后简称": "ST甲"},
        {"变更日期": "2022-05-06", "证券代码": "000100", "证券简称": "x", "变更前简称": "ST甲", "变更后简称": "甲公司"},
    ])
    changes = normalize_sz_name_changes(raw, "r", "t")
    intervals = pd.concat([
        risk_intervals_from_names(changes, {"000100": "甲公司"}, "r", "szse_name_change"),
        risk_intervals_from_names(changes.iloc[0:0], {"600100": "*ST乙"}, "r", "current_name_only"),
    ], ignore_index=True)
    rows = intervals.sort_values(["symbol", "start_date"], na_position="first").to_dict("records")
    assert [(r["symbol"], r["status"], r["start_date"], r["end_date"], r["method"]) for r in rows] == [
        ("000100", "*ST", date(2020, 5, 6), date(2021, 5, 6), "szse_name_change"),
        ("000100", "ST", date(2021, 5, 6), date(2022, 5, 6), "szse_name_change"),
        ("600100", "*ST", None, None, "current_name_only"),
    ]
