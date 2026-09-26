"""Stage-3 reference data: share capital, dividends, Shenwan industry,
CSIndex weights and total-return indices (normalizers, planners, pipeline)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fakes import FakeProvider, SW_MAPPING, at, make_config
from quant_system.data_platform.config import IndexConfig
from quant_system.data_platform.corporate import (
    UNCLASSIFIED,
    derive_sw2014_mapping,
    dividend_report_dates,
    load_sw2014_mapping,
    merge_share_capital,
    normalize_dividends,
    normalize_share_capital,
    normalize_sw_classification,
    share_capital_windows,
)
from quant_system.data_platform.normalization import normalize_index_bars
from quant_system.data_platform.pipeline import IngestionPipeline
from quant_system.data_platform.rebuild import rebuild_canonical
from quant_system.data_platform.storage import read_canonical, write_raw_frame

SATURDAY_NIGHT = at(date(2026, 9, 26), 0, 30)


def test_share_capital_keeps_a_shares_and_the_latest_fetch_of_each_change() -> None:
    raw = pd.DataFrame(FakeProvider().share_rows)
    first = normalize_share_capital(raw, "20260901T000000Z", "t1")
    assert sorted(first["symbol"].unique()) == ["000001", "600001"]  # NEEQ row dropped
    assert first.loc[first["change_date"] == date(2026, 9, 15), "notice_date"].item() == date(2026, 9, 10)
    raw.loc[1, "LISTED_A_SHARES"] = 1.1e9  # the vendor corrects a row
    second = normalize_share_capital(raw, "20260902T000000Z", "t2")
    merged = merge_share_capital(first, second)
    assert len(merged) == len(first)
    assert merged.loc[merged["change_date"] == date(2026, 9, 15), "float_a_shares"].item() == 1.1e9


def test_dividends_parse_ex_dates_and_keep_progress_as_fetched() -> None:
    frame = normalize_dividends(pd.DataFrame(FakeProvider().dividend_rows), "2025-12-31", "r", "t")
    row = frame.iloc[0]
    assert (row["ex_date"], row["cash_per_10"], row["bonus_per_10"]) == (date(2026, 6, 17), 3.0, 2.0)
    assert pd.isna(row["transfer_per_10"]) and row["progress"] == "实施分配"


def test_fetch_windows_backfill_once_and_refresh_recent() -> None:
    first = share_capital_windows("2026-09-26", None)
    assert first[0] == ("END_DATE", "1990-01-01", "1991-01-01")
    assert first[-1] == ("NOTICE_DATE", "2026-07-28", "2026-09-27")
    log = pd.DataFrame([{"dataset": "share_capital", "window": "_".join(w), "rows": 1, "run_id": "r"}
                        for w in first])
    again = share_capital_windows("2026-09-27", log)
    assert [w[1] for w in again if w[0] == "END_DATE"] == ["2026-01-01", "2027-01-01"]
    dates = dividend_report_dates("2026-09-26", None)
    assert dates[0] == "2015-12-31" and dates[-1] == "2026-06-30"
    log = pd.DataFrame([{"dataset": "dividends", "window": f"report_{d}", "rows": 1, "run_id": "r"} for d in dates])
    assert dividend_report_dates("2026-09-26", log)[0] == "2025-03-31"  # within ~18 months (544 days)


def _sw(rows):
    return pd.DataFrame([{"股票代码": s, "计入日期": d, "行业代码": c, "更新日期": u} for s, d, c, u in rows])


CODES = pd.DataFrame([
    {"行业代码": c, "一级行业名称": n, "二级行业名称": None, "三级行业名称": None}
    for c, n in (("340000", "食品饮料"), ("340501", "食品饮料"), ("630000", "电力设备"), ("630101", "电力设备"),
                 ("220000", "基础化工"), ("220101", "基础化工"))
])


def test_sw_mapping_uses_transitions_then_level2_majority() -> None:
    history = _sw([
        ("600001", "2014-02-21", "340301", "2015"), ("600001", "2021-07-30", "340501", "2022"),
        ("600003", "2014-02-21", "220303", "2015"), ("600003", "2021-07-30", "630101", "2022"),
        ("600004", "2014-02-21", "220303", "2015"), ("600004", "2021-07-30", "630101", "2022"),
        ("600005", "2014-02-21", "220304", "2015"), ("600005", "2021-07-30", "220101", "2022"),
        ("600006", "2014-02-21", "220399", "2015"),  # delisted before 2021: no transition of its own
    ])
    mapping = derive_sw2014_mapping(history, CODES).set_index("sw2014_code")
    assert mapping.loc["340301", ["l1_code", "method"]].tolist() == ["340000", "transition"]
    assert mapping.loc["220303", "l1_code"] == "630000"  # the stocks moved to 电力设备
    assert mapping.loc["220399", ["l1_code", "method"]].tolist() == ["630000", "level2_majority"]


def test_sw_intervals_are_dated_and_mapped() -> None:
    history = _sw([
        ("600001", "2014-02-21 00:00:00", "340301", "2015-10-27"),
        ("600001", "2021-07-30 00:00:00", "340501", "2022-05-09"),
        ("600001", "2021-07-30 00:00:00", "340501", "2023-01-01"),  # re-issued: latest update wins
        ("600002", "2016-01-04 00:00:00", "999999", "2016-01-04"),
    ])
    intervals, missing = normalize_sw_classification(history, CODES, {"340301": "340000"}, "r", "t")
    first = intervals[intervals["symbol"] == "600001"].reset_index(drop=True)
    assert first[["start_date", "end_date"]].values.tolist() == [[date(2014, 2, 21), date(2021, 7, 30)],
                                                                 [date(2021, 7, 30), None]]
    assert first["l1_code"].tolist() == ["340000", "340000"] and first["l1_name"].tolist() == ["食品饮料"] * 2
    assert first["mapping"].tolist() == ["sw2014_to_sw2021_l1", "native"]
    assert missing == ["999999"]
    assert intervals.loc[intervals["symbol"] == "600002", "l1_code"].item() == UNCLASSIFIED


def test_committed_mapping_covers_codes_active_since_2020() -> None:
    mapping = load_sw2014_mapping(SW_MAPPING)
    assert len(mapping) > 250 and mapping["340301"] == "340000" and mapping["480101"] == "480000"


def test_csindex_total_return_bars_use_close_for_missing_ohlc() -> None:
    raw = FakeProvider().fetch_csindex_daily("H00300", "20260901", "20260903").frame
    frame = normalize_index_bars(raw, "H00300", "沪深300全收益", "2026-09-01", "2026-09-30", "r", "t",
                                 "akshare.stock_zh_index_hist_csindex.csindex")
    row = frame.iloc[0]
    assert row["open"] == row["high"] == row["low"] == row["close"] == 6000.0
    assert row["volume_shares"] == 2.0e10 and row["turnover_cny"] == pytest.approx(5.0e11)


def _pipeline(root, provider, **config):
    config.setdefault("indices", (IndexConfig("sh000001", "上证指数"),
                                  IndexConfig("H00300", "沪深300全收益", "csindex", "total_return")))
    return IngestionPipeline(root, make_config(**config), provider=provider, clock=lambda: SATURDAY_NIGHT)


def test_pipeline_ingests_reference_data_and_rebuilds_it(tmp_path) -> None:
    provider = FakeProvider()
    manifest = _pipeline(tmp_path, provider).run()
    assert manifest["status"] == "complete"
    shares = read_canonical(tmp_path, "share_capital")
    industry = read_canonical(tmp_path, "industry_sw")
    assert set(shares["symbol"]) == {"600001", "000001"}
    assert read_canonical(tmp_path, "dividends")["ex_date"].tolist() == [date(2026, 6, 17)]
    assert read_canonical(tmp_path, "index_weights")["weight"].sum() == pytest.approx(1.0)
    total_return = read_canonical(tmp_path, "index_bars", "symbol=H00300")
    assert 6000.0 in set(total_return["close"]) and date(1990, 1, 1) not in set(total_return["trade_date"])
    assert industry.loc[industry["symbol"] == "600001", "l1_code"].tolist() == ["340000", "340000"]
    sw_raw = sorted((tmp_path / "data" / "raw" / "akshare" / "industry_sw").glob("run_id=*"))
    # A second run with unchanged workbooks and weights stores no new raw copies.
    manifest = _pipeline(tmp_path, provider).run()
    assert manifest["status"] == "complete"
    assert sorted((tmp_path / "data" / "raw" / "akshare" / "industry_sw").glob("run_id=*")) == sw_raw
    assert len(list((tmp_path / "data" / "raw" / "akshare" / "index_weights").glob("run_id=*"))) == 1
    assert read_canonical(tmp_path, "industry_sw")["run_id"].unique().tolist() == [sw_raw[0].name[7:]]
    report = rebuild_canonical(tmp_path, make_config(
        indices=(IndexConfig("sh000001", "上证指数"), IndexConfig("H00300", "沪深300全收益", "csindex", "total_return"))))
    for dataset in ("share_capital", "dividends", "industry_sw", "index_weights", "corporate_fetch_log"):
        assert report["diff"][dataset]["live"] == report["diff"][dataset]["rebuilt"], dataset
        live = read_canonical(tmp_path, dataset)
        rebuilt = pd.read_parquet(tmp_path / report["staging"] / "data" / "canonical" / dataset / "data.parquet")
        pd.testing.assert_frame_equal(live, rebuilt, check_dtype=False)


def test_sw_download_failure_keeps_the_existing_classification(tmp_path) -> None:
    provider = FakeProvider()
    _pipeline(tmp_path, provider).run()
    before = read_canonical(tmp_path, "industry_sw")
    provider.fail_sw = True
    manifest = _pipeline(tmp_path, provider).run()
    assert manifest["status"] == "complete"
    pd.testing.assert_frame_equal(read_canonical(tmp_path, "industry_sw"), before)


def test_raw_writer_stores_mixed_type_columns_as_text(tmp_path) -> None:
    frame = pd.DataFrame({"a": [1, "x", None], "b": [1.0, 2.0, 3.0]})
    path = write_raw_frame(tmp_path, "akshare", "test", "20260101T000000Z", "mixed", frame)
    assert pd.read_parquet(path)["a"].tolist() == ["1", "x", None]


def test_concurrent_pages_are_assembled_in_order_and_counted() -> None:
    import time as _time

    from quant_system.data_platform.providers.eastmoney_dc import PaginationMismatch, collect_pages

    def fetch(report, filter, page, sort_columns, sort_types, total=25):
        _time.sleep(0.01 * (6 - page % 6))  # later pages may finish first
        rows = [{"k": i} for i in range((page - 1) * 5, min(page * 5, total))]
        return rows, 5, total

    frame = collect_pages(fetch, "R", "(x)", "SECUCODE,REPORT_DATE", workers=4)
    assert frame["k"].tolist() == list(range(25))
    with pytest.raises(PaginationMismatch):
        collect_pages(lambda **kw: fetch(**kw, total=30) if kw["page"] == 1 else fetch(**kw), "R", "(x)", "S",
                      workers=2)
