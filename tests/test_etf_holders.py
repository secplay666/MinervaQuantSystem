"""The top-holder table of ETF periodic reports: the layouts seen in 156 real reports."""

from __future__ import annotations

from datetime import date

from quant_system.data_platform.etf_holders import classify_holder, parse_top_holders, report_period

HEAD = "9.2 期末上市基金前十名持有人 ...... 75\n目录之后的正文\n9.2 期末上市基金前十名持有人\n\n"
TAIL = "\n9.3 期末基金管理人的从业人员持有本基金的情况\n  项目   100.00   0.00%\n"


def names(text: str) -> list[str]:
    return [r["holder"] for r in parse_top_holders(HEAD + text + TAIL)]


def test_names_wrapped_above_and_below_their_row() -> None:  # 华泰柏瑞 510300 2025 年报
    text = """    序号          持有人名称      持有份额（份）      占上市总份额比例（%）

      1        中央汇金资产管理有  37,858,474,974.00                        42.62
                  限责任公司

      6        大家人寿保险股份有    190,696,200.00                        0.21
                限公司－传统产品

              北京诚旸投资有限公

      7        司－诚旸灵活配置私    179,399,890.00                        0.20
                募证券投资基金

注：前十名持有人为除本基金的联接基金之外的前十名持有人。"""
    rows = parse_top_holders(HEAD + text + TAIL)
    assert [r["holder"] for r in rows] == ["中央汇金资产管理有限责任公司", "大家人寿保险股份有限公司－传统产品",
                                           "北京诚旸投资有限公司－诚旸灵活配置私募证券投资基金"]
    assert rows[0]["shares"] == 37_858_474_974 and rows[0]["pct"] == 42.62


def test_percent_signs_header_remnants_and_names_ending_in_a_number() -> None:  # 华夏 / 159845
    text = """        序号                持有人名称                  持有份额（份）      占上市总份额

                                                                                  比例

          1      中央汇金投资有限责任公司                  28,791,513,899.00        50.81%

    4      海港人寿保险股份有限公司－传统 2              103,738,400.00          0.65%

    8      招商基金－农业银行－北京大学教育                    5,234,101.00              1.08%
      基金会

      上海柏泰华盈私募基金管理有限公司

 9    －柏泰华盈常青藤套利 2 号私募证券                    4,914,752.00              1.02%
      投资基金

    11      交易型开放式指数证券投资基金发起              52,078,888.00          0.33%
            式联接基金"""
    rows = parse_top_holders(HEAD + text + TAIL)
    assert [r["holder"] for r in rows] == [
        "中央汇金投资有限责任公司", "海港人寿保险股份有限公司－传统2", "招商基金－农业银行－北京大学教育基金会",
        "上海柏泰华盈私募基金管理有限公司－柏泰华盈常青藤套利2号私募证券投资基金",
        "交易型开放式指数证券投资基金发起式联接基金"]
    assert [r["pct"] for r in rows] == [50.81, 0.65, 1.08, 1.02, 0.33]
    assert rows[1]["shares"] == 103_738_400


def test_share_counts_that_wrap_like_the_tianhong_reports() -> None:  # 天弘 515330 / 159360
    text = """ 序                    持有人名称                    持有份额  占上市总
 号                                                    (份)    份额比例

 1  中国平安人寿保险股份有限公司－自有资金          389,586,30      6.33%
                                                          0.00

    长江养老保险股份有限公司－中国太平洋人寿权益基  47,222,698.

 3  金型投资产品（保额分红）委托专户                        00    1.65%

    中国人寿保险（集团）公司企业年金计划－中国农业银 38,863,300.

 4  行股份有限公司                                          00    1.36%

 -  平安银行股份有限公司－天弘中证A500交易型开放式  2,323,542,8    81.12%
    指数证券投资基金联接基金                              67.00

  注：持有人为场内持有人。"""
    rows = parse_top_holders(HEAD + text + TAIL)
    assert [(r["rank"], r["shares"], r["pct"]) for r in rows] == [
        (1, 389_586_300, 6.33), (3, 47_222_698, 1.65), (4, 38_863_300, 1.36), (4, 2_323_542_867, 81.12)]
    assert rows[1]["holder"] == "长江养老保险股份有限公司－中国太平洋人寿权益基金型投资产品（保额分红）委托专户"
    assert rows[2]["holder"] == "中国人寿保险（集团）公司企业年金计划－中国农业银行股份有限公司"
    assert rows[3]["holder"].endswith("联接基金")


def test_a_name_taken_by_the_previous_holder_is_given_back() -> None:  # 景顺长城 159353
    text = """ 9  国华人寿保险股份有限公司－传统三号              12,000,000.00      0.50%
    中国工商银行股份有限公司

 -  －景顺长城中证A500交易型开放式指数证券投资基金联接基金  900,000,000.00  37.00%"""
    assert names(text) == ["国华人寿保险股份有限公司－传统三号",
                           "中国工商银行股份有限公司－景顺长城中证A500交易型开放式指数证券投资基金联接基金"]


def test_reports_without_the_table_and_titles() -> None:
    assert parse_top_holders("§8 基金份额持有人信息\n8.1 期末基金份额持有人户数及持有人结构\n") == []
    assert report_period("某ETF2026年中期报告") == (date(2026, 6, 30), "interim")
    assert report_period("某ETF:2013年半年度报告") == (date(2013, 6, 30), "interim")
    assert report_period("某ETF2025年年度报告") == (date(2025, 12, 31), "annual")
    assert report_period("某ETF2025年年度报告摘要") is None and report_period("某ETF基金产品资料概要") is None
    classes = {"huijin_asset": ["中央汇金资产管理"], "huijin_investment": ["中央汇金投资"]}
    assert classify_holder("易方达基金－中央汇金投资有限责任公司－易方达基金－汇金资管单一资产管理计划", classes) \
        == "huijin_investment"
    assert classify_holder("中国人寿保险股份有限公司", classes) is None
