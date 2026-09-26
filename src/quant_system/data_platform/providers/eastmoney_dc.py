"""Eastmoney datacenter (``datacenter-web``) and other direct HTTP sources.

Eastmoney's quote hosts are blocked from this deployment, but the
datacenter API works (ADR-003).  Every report is fetched with
``columns=ALL`` and stored raw; normalizers pick fields by their vendor
names, so AKShare's positional renaming (which drops fields) is avoided.
"""

from __future__ import annotations

import io
import warnings

import pandas as pd
import requests

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DC_PAGE_SIZE = 500  # the service caps pageSize at 500
# A shares and CDRs; excludes B shares (058001002) and NEEQ (058001005).
A_SHARE_TYPES = '(SECURITY_TYPE_CODE in ("058001001","058001008"))'
_EMPTY_MESSAGES = ("返回数据为空",)
REQUEST_TIMEOUT_SECONDS = 60
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0 Safari/537.36")

SW_BASE_URL = "https://www.swsresearch.com/swindex/pdf/SwClass2021/"
SW_FILES = {"history": "StockClassifyUse_stock.xls", "codes": "SwClassCode_2021.xls"}
SW_COLUMNS = {"history": ("股票代码", "计入日期", "行业代码", "更新日期"),
              "codes": ("行业代码", "一级行业名称", "二级行业名称", "三级行业名称")}


class PaginationMismatch(RuntimeError):
    """Rows collected differ from the count the service reported (pages
    shifted while fetching); retrying the whole report usually fixes it."""


def dc_page(report: str, filter: str, page: int, sort_columns: str, sort_types: str) -> tuple[list[dict], int, int]:
    """One datacenter page -> (rows, pages, count); an empty result is ([], 0, 0)."""
    params = {
        "reportName": report, "columns": "ALL", "filter": filter, "pageNumber": str(page),
        "pageSize": str(DC_PAGE_SIZE), "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    response = requests.get(DATACENTER_URL, params=params,
                            headers={"User-Agent": _BROWSER_UA, "Referer": "https://data.eastmoney.com/"},
                            timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        message = str(payload.get("message") or "")
        if any(text in message for text in _EMPTY_MESSAGES):
            return [], 0, 0
        raise ValueError(f"datacenter {report} rejected the query: {message}")
    result = payload.get("result") or {}
    return list(result.get("data") or []), int(result.get("pages") or 0), int(result.get("count") or 0)


def collect_pages(fetch_page, report: str, filter: str, sort_columns: str) -> pd.DataFrame:
    """Fetch every page of a report.  ``fetch_page(page)`` returns
    ``dc_page``'s tuple; the row count is checked against the service."""
    sort_types = ",".join("1" for _ in sort_columns.split(","))
    rows, page, pages, count = [], 1, 1, None
    while page <= pages:
        data, pages, total = fetch_page(report=report, filter=filter, page=page, sort_columns=sort_columns,
                                        sort_types=sort_types)
        count = total if count is None else count
        rows.extend(data)
        page += 1
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.drop_duplicates(ignore_index=True)
    if count and len(frame) < count:
        raise PaginationMismatch(f"{report} {filter}: collected {len(frame)} of {count} rows")
    return frame


def sw_file(kind: str) -> pd.DataFrame:
    """Shenwan classification workbook (``history`` or ``codes``) as text.

    The site serves an incomplete certificate chain, so verification is
    disabled for this host only; the schema check below guards the content.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        response = requests.get(SW_BASE_URL + SW_FILES[kind], verify=False, timeout=REQUEST_TIMEOUT_SECONDS,
                                headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.swsresearch.com/"})
    response.raise_for_status()
    frame = pd.read_excel(io.BytesIO(response.content), dtype=str)
    missing = [column for column in SW_COLUMNS[kind] if column not in frame.columns]
    if missing:
        raise ValueError(f"Shenwan {kind} workbook lacks columns {missing}: {list(frame.columns)}")
    return frame
