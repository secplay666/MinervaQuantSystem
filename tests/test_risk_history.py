from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant_system.data_platform.risk_history import (
    HistoryContext,
    bulletin_windows,
    build_exchange_history,
    classify_title,
    effective_date,
    merge_bulletins,
    normalize_bse_announcements,
    normalize_sse_bulletins,
    quarter_windows,
    reconcile_with_prices,
)


@pytest.mark.parametrize(
    ("title", "exchange", "star", "st", "kind"),
    [
        ("关于公司股票被实施退市风险警示及叠加其他风险警示暨停牌的公告", "SSE", "set", "set", "change"),
        ("关于撤销退市风险警示并实施其他风险警示暨停牌的公告", "SSE", "revoke", "set", "change"),
        ("关于撤销退市风险警示并继续实施其他风险警示暨临时停牌的公告", "SSE", "revoke", "assert", "change"),
        ("关于撤销退市风险警示及其他风险警示暨停牌的公告", "SSE", "revoke", "revoke", "change"),
        ("大唐电信科技股份有限公司关于公司股票撤销风险警示的公告", "SSE", "revoke", "revoke", "change"),
        ("关于撤销公司股票因重整而被实施的退市风险警示暨继续被实施退市风险警示及其他风险警示的公告",
         "SSE", "assert", "assert", "assert"),
        ("吉林华微电子股份有限公司关于撤销部分其他风险警示暨继续被实施退市风险警示及其他风险警示的公告",
         "SSE", "assert", "assert", "assert"),
        ("关于公司股票撤销退市风险警示的公告", "SSE", "revoke", "revoke_implied", "change"),
        ("关于公司股票可能被实施退市风险警示的第二次提示性公告", "SSE", None, None, "ignore"),
        ("关于公司股票将被实施退市风险警示的第三次提示性公告", "SSE", None, None, "ignore"),
        ("[临时公告]康乐卫士:关于公司股票交易将被实施退市风险警示的公告", "BSE", "set", None, "change"),
        ("关于股票被实施其他风险警示相关事项的进展公告", "SSE", None, None, "ignore"),
        ("关于公司股票继续实施其他风险警示的公告", "SSE", None, "assert", "assert"),
        ("关于变更公司证券简称的公告", "SSE", None, None, "ignore"),
    ],
)
def test_title_classifier(title, exchange, star, st, kind) -> None:
    result = classify_title(title, exchange)
    assert (result["star"], result["st"], result["kind"]) == (star, st, kind)


def _sessions(start: str, count: int) -> list[str]:
    days = pd.bdate_range(start, periods=count)
    return [d.strftime("%Y-%m-%d") for d in days]


def test_effective_date_is_the_first_bar_after_the_suspension() -> None:
    sessions = _sessions("2024-04-01", 20)
    bars = [d for d in sessions if d != "2024-04-08"]  # suspended on the implementation day
    ctx = HistoryContext(sessions, {"600001": bars})
    assert effective_date(ctx, "600001", "2024-04-06", "关于实施退市风险警示暨停牌的公告", "SSE") == \
        ("2024-04-09", "bars:first_bar_after_suspension")
    # BSE publishes in the evening: suspension on the next session.
    ctx = HistoryContext(sessions, {"920001": [d for d in sessions if d != "2024-04-09"]})
    assert effective_date(ctx, "920001", "2024-04-08", "[临时公告]x:关于股票将被实施退市风险警示的公告", "BSE")[0] == \
        "2024-04-10"


def _bulletins(rows):
    return pd.DataFrame(rows, columns=["exchange", "symbol", "pub_date", "title", "security_name", "bulletin_id"])


def test_duplicate_notices_collapse_and_change_notices_are_not_name_evidence() -> None:
    sessions = _sessions("2024-01-01", 120)
    bars = [d for d in sessions if d not in {"2024-02-05", "2024-04-15"}]
    ctx = HistoryContext(sessions, {"600001": bars})
    bulletins = _bulletins([
        # board notice and the exchange-approved 暨停牌 notice of the same change
        ("SSE", "600001", "2024-02-01", "甲公司关于公司股票将被实施其他风险警示的提示", "甲公司", "1"),
        ("SSE", "600001", "2024-02-03", "甲公司关于公司股票实施其他风险警示暨停牌的公告", "甲公司", "2"),
        ("SSE", "600001", "2024-03-01", "ST甲关于日常经营的公告", "ST甲", "3"),
        # the revoking notice still carries the old name: not evidence of ST afterwards
        ("SSE", "600001", "2024-04-13", "ST甲关于撤销其他风险警示暨停牌的公告", "ST甲", "4"),
    ])
    history = build_exchange_history(ctx, bulletins, "SSE", {})
    rows = history.intervals[["status", "start_date", "end_date"]].values.tolist()
    assert rows == [["ST", "2024-02-06", "2024-04-16"]]
    assert history.summary["remaining_name_mismatches"] == 0


def test_every_file_of_an_sse_disclosure_package_is_kept() -> None:
    # 603039 on 2022-04-29: the implementation notice shares ORG_BULLETIN_ID
    # with the board statements released alongside it.
    raw = pd.DataFrame({
        "SECURITY_CODE": ["603039", "603039"],
        "SSEDATE": ["2022-04-29", "2022-04-29"],
        "TITLE": ["泛微网络关于股票实施其他风险警示暨公司股票停牌的提示性公告",
                  "泛微网络董事会关于会计师事务所出具否定意见内部控制审计报告涉及事项的专项说明"],
        "SECURITY_NAME": ["泛微网络", "泛微网络"],
        "ORG_BULLETIN_ID": ["8245264204414486", "8245264204414486"],
        "URL": ["/c/new/2022-04-29/603039_20220429_21_42WRZKLf.pdf", "/c/new/2022-04-29/603039_20220429_22_08YaGGz7.pdf"],
    })
    frame = merge_bulletins(None, normalize_sse_bulletins(raw, "风险警示", "r", "2.0"))
    assert len(frame) == 2
    sessions = _sessions("2022-04-25", 20)
    bars = [d for d in sessions if d != "2022-04-29"]  # suspended on the publication day, as in the data
    history = build_exchange_history(HistoryContext(sessions, {"603039": bars}), frame, "SSE", {})
    assert history.intervals[["status", "start_date"]].values.tolist() == [["ST", "2022-05-02"]]
    # The board statement published with the notice carries the pre-change
    # name: it is not evidence against the change.
    assert history.summary["remaining_name_mismatches"] == 0


def test_price_reconciliation_trims_drops_and_extends() -> None:
    sessions = _sessions("2020-01-01", 200)
    intervals = pd.DataFrame([
        {"symbol": "600001", "status": "*ST", "start_date": "2013-01-01", "end_date": "2020-09-01",
         "method": "initial_from_first_event", "source": "sse_bulletin", "start_is_lower_bound": True,
         "start_title": None, "end_title": None},
        {"symbol": "600002", "status": "ST", "start_date": "2020-01-01", "end_date": None,
         "method": "x", "source": "sse_bulletin", "start_is_lower_bound": False, "start_title": None, "end_title": None},
        {"symbol": "600003", "status": "ST", "start_date": "2020-06-01", "end_date": "2020-07-01",
         "method": "repair:evidence_upper_bound|low=2014-01-01|high=2020-06-01", "source": "sse_bulletin",
         "start_is_lower_bound": False, "start_title": None, "end_title": None},
    ])
    evidence = pd.DataFrame([
        # 600001: band breaks early in 2020, 5% closes afterwards -> start moves after the last break
        ("600001", "2020-02-03", True, False), ("600001", "2020-03-02", True, False),
        ("600001", "2020-05-04", False, True), ("600001", "2020-05-05", False, True), ("600001", "2020-06-01", False, True),
        # 600002: breaks, and the only "support" days also break the band -> dropped
        ("600002", "2020-02-03", True, True), ("600002", "2020-04-01", True, False),
        # 600003: 5% closes before the uncertain start and no breaks -> start extended to the data start
        ("600003", "2020-02-10", False, True), ("600003", "2020-03-10", False, True),
    ], columns=["symbol", "trade_date", "beyond", "at_limit"])
    result, log = reconcile_with_prices(intervals, evidence, sessions, data_start="2020-01-01")
    actions = dict(zip(log["symbol"], log["action"]))
    assert actions == {"600001": "start_moved", "600002": "dropped", "600003": "start_extended"}
    starts = dict(zip(result["symbol"], result["start_date"]))
    assert starts == {"600001": "2020-03-03", "600003": "2020-01-01"}


def test_fetch_windows_cover_history_once_and_refresh_recent_quarters() -> None:
    assert quarter_windows("2013-01-01", "2013-05-10") == [("2013-01-01", "2013-03-31"), ("2013-04-01", "2013-05-10")]
    first = bulletin_windows("2026-09-26", None)
    assert ("SSE", "风险警示", "2013-01-01", "2013-03-31") in first
    assert ("BSE", "退市整理", "2021-11-15", "2021-12-31") in first
    log = pd.DataFrame(first, columns=["exchange", "keyword", "window_start", "window_end"])
    again = bulletin_windows("2026-09-27", log)
    assert ("SSE", "风险警示", "2013-01-01", "2013-03-31") not in again
    assert ("SSE", "风险警示", "2026-07-01", "2026-09-27") in again  # current quarter refreshed
    assert all(end >= "2026-05-30" for _, _, _, end in again if (_, _) != ("", ""))


def test_bse_codes_are_mapped_only_when_the_company_matches() -> None:
    master = pd.DataFrame({"symbol": ["920305", "920680"], "name": ["云创退", "*ST广道"], "exchange": "BSE"})
    raw = pd.DataFrame({
        "companyCd": ["835305", "839680", "831340", "920305"],
        "companyName": ["*ST云创", "广道数字", "金童股份", "云创数据"],
        "disclosureTitle": ["t1", "t2", "t3", "t4"], "destFilePath": ["a", "b", "c", "d"],
        "publishDate": ["2025-05-06", "2025-05-06", "2025-05-06", "2026-07-08"],
    })
    frame, unmapped = normalize_bse_announcements(raw, "风险警示", "r", "2.0", master)
    assert frame["symbol"].tolist() == ["920305", "920305"] and unmapped == 2
    # 839680 -> 920680 is rejected because 广道数字 != 广道 (name changed): kept out rather than guessed.
