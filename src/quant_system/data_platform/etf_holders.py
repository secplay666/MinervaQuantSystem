"""Top holders of broad-index ETFs from their annual and interim reports.

Fund reports list the ten largest holders of a listed fund at the period end
("期末上市基金前十名持有人", besides the fund's own feeder fund).  The report
text comes from the announcement's text rendering, a layout-preserved table
whose holder names often wrap over two or three lines; ``parse_top_holders``
rebuilds the names.  Holdings are as of the period end and known from the
notice date.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from .normalization import _lineage, as_date

__all__ = [
    "HOLDER_COLUMNS",
    "SOURCE_FUND_REPORT",
    "classify_holder",
    "holder_rows",
    "merge_holders",
    "parse_top_holders",
    "report_period",
    "reports_to_fetch",
    "lists_to_refresh",
]

SOURCE_FUND_REPORT = "eastmoney.np-cnotice-fund.ann"
HOLDER_COLUMNS = [
    "symbol", "report_date", "report_type", "notice_date", "art_code", "rank", "holder", "shares", "pct",
    "feeder", "source", "ingested_at", "run_id", "schema_version",
]
REPORTS_START = date(2015, 1, 1)
LIST_REFRESH_DAYS = 7

_TITLE = re.compile(r"(\d{4})\s*年\s*(年度报告|半年度报告|中期报告)")
_SKIP_TITLE = re.compile(r"摘要|英文|English|提示|更正公告")
_SECTION = re.compile(r"期末上市基金前十名持有人")
_ROW = re.compile(r"^\s*(\d{1,2})\s+(.*?)\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2})\s+(\d+(?:\.\d+)?)\s*%?\s*$")
_END = re.compile(r"^\s*(\d+\.\d+\s+\S|§|注[:：])")
_NOISE = re.compile(r"序号|持有人名称|持有份额|份额比例|年度报告|中期报告|半年度报告|第\s*\d+\s*页|^[\s比例（）()%]+$|^\s*号(\s|$)")
# A name that is already whole: the next line starts another holder.  (Not 资金: "…集合资金" goes on
# with "信托计划".)  Two to four characters alone are a person's name.
_COMPLETE = re.compile(r"(公司|产品|基金|基金会|计划|账户|组合|传统|分红|万能|投连|中心|银行|理财|自有资金|[）)]|"
                       r"\b(?:AG|PLC|plc|LLC|Limited|LIMITED|Ltd\.?|LTD\.?|Inc\.?|INC\.?|N\.A\.|CO\.,? ?LTD\.?))$"
                       r"|^[\u4e00-\u9fa5·]{2,4}$")


def report_period(title: str) -> tuple[date, str] | None:
    """(period end, annual | interim) of a periodic report title, or None."""
    if _SKIP_TITLE.search(title):
        return None
    match = _TITLE.search(title)
    if not match:
        return None
    year = int(match.group(1))
    return (date(year, 12, 31), "annual") if match.group(2) == "年度报告" else (date(year, 6, 30), "interim")


def parse_top_holders(text: str) -> list[dict]:
    """Rows (rank, holder, shares, pct) of the top-holder table; [] if the
    report has none (the 2026 interim reports left it out).

    The table is a layout-preserved rendering: cells are separated by runs
    of spaces, and a narrow cell wraps, so a holder's name and even its
    share count may continue on the next lines.  A record is complete once
    it has a rank, a percentage and a share count with two decimals; a line
    with only a name either continues the previous (unfinished) name or
    starts the next one.
    """
    starts = [m.end() for m in _SECTION.finditer(text)]
    if not starts:
        return []
    rows: list[dict] = []
    current: dict | None = None
    pending: list[str] = []
    appended = ""  # the last name-only line added to the previous holder
    for line in text[starts[-1]:].splitlines()[1:]:  # the last match is the body, the first the contents
        if _END.match(line):
            break
        if not line.strip() or _NOISE.search(line):
            continue
        rank, name, shares, pct = _cells(line)
        if not shares and pct is None and rank is None:  # a line with only a name
            if current is not None:
                current["holder"] += name
            elif rows and _continues(rows[-1]["holder"], name):
                rows[-1]["holder"] += name
                appended = name
            else:
                pending.append(name)
            continue
        if current is None:
            current = {"rank": None, "holder": "".join(pending), "shares": "", "pct": None}
            if not pending and name[:1] in "-－" and appended and _COMPLETE.search(appended) \
                    and rows and rows[-1]["holder"].endswith(appended):
                # "…传统三号" took the next holder's first line ("中国工商银行股份有限公司"); give it back.
                rows[-1]["holder"] = rows[-1]["holder"][: -len(appended)]
                current["holder"] = appended
            pending, appended = [], ""
        if rank is not None:
            current["rank"] = rank
        current["holder"] += name
        current["shares"] += shares
        if pct is not None:
            current["pct"] = pct
        if current["rank"] is not None and current["pct"] is not None and _SHARES.match(current["shares"]):
            rows.append({"rank": current["rank"] if current["rank"] != "-" else len(rows) + 1,
                         "holder": current["holder"], "shares": float(current["shares"].replace(",", "")),
                         "pct": current["pct"]})
            current = None
        if len(rows) > 40:  # not a holder table
            return []
    return rows


_SHARES = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}$")
_NUMBER = re.compile(r"^[\d,]*\.?\d*$")
_PCT = re.compile(r"^(\d+(?:\.\d+)?)%$")
_RANK = re.compile(r"^(\d{1,2}|[-－—])(?:\s+(\S.*))?$")
_NATIONAL_START = re.compile(r"^(中央汇金|中国证券金融|梧桐树)")
# Only a share-count piece (with a comma or a point) is split off: "…传统 2" and "中证 1000" are names.
_TRAILING_NUMBER = re.compile(r"^(.*?[^\d,.\s])\s+(\d[\d,]*[,.][\d,.]*%?)$")


def _cells(line: str) -> tuple[object, str, str, float | None]:
    """(rank, name fragment, share-count fragment, percentage) of one line."""
    chunks = []
    for chunk in (c for c in re.split(r"\s{2,}", line.strip()) if c):
        split = _TRAILING_NUMBER.match(chunk)  # "…中国农业银 38,863,300.": one space before the number
        chunks += [split.group(1), split.group(2)] if split else [chunk]
    rank = None
    numeric = any(_PCT.match(c) or (_NUMBER.match(c) and any(ch.isdigit() for ch in c)) for c in chunks[1:])
    if chunks and chunks[0][:1] in "-－—" and numeric:
        # The feeder fund's row is ranked "-", sometimes with no space before the name.
        rest = chunks[0][1:].strip()
        return (_finish_cells("-", [rest, *chunks[1:]] if rest else chunks[1:]))
    if chunks:
        match = _RANK.match(chunks[0])
        if match and (match.group(2) is None or not _NUMBER.match(match.group(2).replace(" ", ""))):
            rank = int(match.group(1)) if match.group(1).isdigit() else "-"
            chunks = chunks[1:] if match.group(2) is None else [match.group(2), *chunks[1:]]
            if match.group(2) is None and not chunks:
                rank = None  # a lone number: a wrapped share count, not a rank
                chunks = [match.group(1)] if match.group(1).isdigit() else []
    return _finish_cells(rank, chunks)


def _finish_cells(rank: object, chunks: list[str]) -> tuple[object, str, str, float | None]:
    names, shares, pct = [], "", None
    for chunk in chunks:
        compact = chunk.replace(" ", "")
        if _PCT.match(compact):
            pct = float(_PCT.match(compact).group(1))
        elif compact and _NUMBER.match(compact) and any(ch.isdigit() for ch in compact):
            if shares and _SHARES.match(shares) and pct is None:
                pct = float(compact)  # a percentage without the sign, after a whole share count
            else:
                shares += compact
        else:
            names.append(_squash(chunk))
    return rank, "".join(names), shares, pct


def _continues(previous: str, fragment: str) -> bool:
    """Whether a name-only line goes on with the previous holder's name."""
    if _NATIONAL_START.match(fragment):
        return False  # these names always start a holder (so their shares are never someone else's)
    if fragment[:1] in "-－(（·/":
        return True
    return not _COMPLETE.search(previous)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def classify_holder(name: str, classes: dict[str, list[str]]) -> str | None:
    """The first class whose patterns match the holder's name."""
    for label, patterns in classes.items():
        if any(re.search(p, name) for p in patterns):
            return label
    return None


def holder_rows(symbol: str, meta: dict, text: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    period = report_period(str(meta["title"]))
    parsed = parse_top_holders(text) if period else []
    if not parsed:
        return pd.DataFrame(columns=HOLDER_COLUMNS)
    frame = pd.DataFrame(parsed)
    frame["symbol"] = symbol
    frame["report_date"] = period[0]
    frame["report_type"] = period[1]
    frame["notice_date"] = as_date(meta["notice_date"])
    frame["art_code"] = str(meta["art_code"])
    frame["feeder"] = frame["holder"].str.contains("联接")
    frame = _lineage(frame, SOURCE_FUND_REPORT, run_id, ingested_at)
    return frame[HOLDER_COLUMNS]


def merge_holders(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    """One table per (fund, period): a later notice (a corrected report) replaces the earlier one."""
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=HOLDER_COLUMNS)
    frame = pd.concat([part.reindex(columns=HOLDER_COLUMNS) for part in parts], ignore_index=True)
    latest = (frame.sort_values(["notice_date", "run_id"], kind="stable")
              .groupby(["symbol", "report_date"])["art_code"].last())
    keep = frame["art_code"] == frame.set_index(["symbol", "report_date"]).index.map(latest)
    frame = frame[keep.to_numpy()].drop_duplicates(["symbol", "report_date", "rank", "holder"], keep="last")
    return frame.sort_values(["symbol", "report_date", "rank"], ignore_index=True)


def lists_to_refresh(symbols: list[str], log: pd.DataFrame | None, today: date) -> list[str]:
    """Funds whose report list was not fetched in the last week."""
    fresh = set()
    if log is not None and not log.empty:
        recent = log[(log["dataset"] == "report_list")]
        for window, run_id in zip(recent["window"], recent["run_id"]):
            if (today - date(int(run_id[:4]), int(run_id[4:6]), int(run_id[6:8]))).days < LIST_REFRESH_DAYS:
                fresh.add(window)
    return [s for s in symbols if s not in fresh]


def reports_to_fetch(listing: pd.DataFrame, log: pd.DataFrame | None) -> list[dict]:
    """Periodic reports (from 2015) in a fund's list not fetched yet, oldest first."""
    done = set() if log is None or log.empty else set(log.loc[log["dataset"] == "report", "window"])
    out = []
    for row in listing.itertuples(index=False):
        period = report_period(str(row.TITLE))
        if period is None or period[0] < REPORTS_START or row.ID in done:
            continue
        out.append({"art_code": str(row.ID), "title": str(row.TITLE), "notice_date": str(row.PUBLISHDATEDesc)[:10],
                    "report_date": period[0]})
    return sorted(out, key=lambda r: (r["report_date"], r["notice_date"]))
