"""Probe (2026-10-06): analyst forecasts and insider/buyback/lock-up data for the money map's gold ring and "company buying".

Runs on the server with the dev env's code (reuses the datacenter page helper).
"""

from __future__ import annotations

import json
import time
import warnings

import requests

from quant_system.data_platform.providers.eastmoney_dc import dc_page

warnings.filterwarnings("ignore")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

DATACENTER = [
    # report, date column, what it is
    ("RPT_SHARE_HOLDER_INCREASE", "NOTICE_DATE", "股东增减持"),
    ("RPT_EXECUTIVE_HOLD_DETAILS", "CHANGE_DATE", "董监高持股变动"),
    ("RPTA_WEB_GETHGLIST_NEW", "UPD", "回购（新）"),
    ("RPTA_WEB_GETHGLIST", "UPD", "回购"),
    ("RPT_LIFT_STAGE", "FREE_DATE", "限售解禁"),
    ("RPT_WEB_RESPREDICT", "", "一致预期（当前）"),
]


def probe_datacenter() -> None:
    for report, date_col, label in DATACENTER:
        started = time.time()
        try:
            rows, pages, count = dc_page(report, "", 1, date_col or "SECURITY_CODE", "1")
            if not rows:
                print(f"{label:12s} {report:28s} EMPTY")
                continue
            first = rows[0].get(date_col) if date_col else ""
            recent, _, _ = dc_page(report, "", 1, date_col or "SECURITY_CODE", "-1")
            last = recent[0].get(date_col) if date_col else ""
            print(f"{label:12s} {report:28s} count={count:>8} earliest={str(first)[:10]} latest={str(last)[:10]} "
                  f"({time.time() - started:.1f}s)")
            print(f"{'':42s} cols={list(rows[0])[:40]}")
            sample = {k: v for k, v in recent[0].items() if v not in (None, "")}
            print(f"{'':42s} sample={json.dumps(sample, ensure_ascii=False)[:600]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label:12s} {report:28s} FAIL {type(exc).__name__}: {str(exc)[:150]}")


def report_list(begin: str, end: str, page: int = 1) -> dict:
    params = {"industryCode": "*", "pageSize": "100", "industry": "*", "rating": "*", "ratingChange": "*",
              "beginTime": begin, "endTime": end, "pageNo": str(page), "fields": "", "qType": "0", "orgCode": "",
              "code": "*", "rcode": "", "p": str(page), "pageNum": str(page), "pageNumber": str(page)}
    response = requests.get("https://reportapi.eastmoney.com/report/list", params=params,
                            headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=30)
    response.raise_for_status()
    return response.json()


def probe_reports() -> None:
    for begin, end in (("2026-09-01", "2026-09-30"), ("2019-03-01", "2019-03-31"), ("2012-03-01", "2012-03-31")):
        started = time.time()
        try:
            payload = report_list(begin, end)
            data = payload.get("data") or []
            print(f"研报 {begin[:7]}: hits={payload.get('hits')} pages={payload.get('TotalPage')} rows={len(data)} "
                  f"({time.time() - started:.1f}s)")
            if data:
                keys = list(data[0])
                print(f"   keys={keys}")
                pick = {k: data[0].get(k) for k in ("title", "stockCode", "stockName", "orgSName", "publishDate",
                                                   "predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps",
                                                   "predictThisYearPe", "indvInduName", "emRatingName", "ratingChange")}
                print(f"   sample={json.dumps(pick, ensure_ascii=False)}")
                with_eps = sum(1 for row in data if row.get("predictNextYearEps") not in (None, ""))
                print(f"   rows with next-year EPS on page 1: {with_eps}/{len(data)}")
        except Exception as exc:  # noqa: BLE001
            print(f"研报 {begin[:7]}: FAIL {type(exc).__name__}: {str(exc)[:150]}")
        time.sleep(1)


if __name__ == "__main__":
    probe_datacenter()
    probe_reports()
