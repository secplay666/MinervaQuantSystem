"""Dated risk-warning (ST / *ST / delisting period) history for SSE and BSE.

SZSE publishes dated short-name changes; SSE and BSE do not.  This module
reconstructs their history from exchange bulletins in four steps:

1. **Events** — bulletin titles mentioning 风险警示 are parsed into two
   independent flags: 退市风险警示 (*ST) and 其他风险警示 (ST).  Pre-warnings,
   progress reports, applications and replies are ignored.
2. **Transitions** — events are walked per symbol; repeated notices of one
   change are collapsed onto the notice with the strongest suspension
   evidence.  The effective date is the first bar after the one-day
   suspension the exchange imposes (SSE: first session on/after the
   disclosure date; BSE: the session after the evening release).
3. **Name evidence** — dated names on SSE bulletins (``SECURITY_NAME``,
   filled from 2021, and ``ST xxx关于…`` title prefixes) and BSE company
   names correct missing notices.  A status-change notice itself carries
   the *pre-change* name, so it is never used as evidence.
4. **Price evidence** (SSE main board before the 2026-07-06 rule change,
   when risk-warned stocks had a 5% limit) — trading beyond the 5% band on
   a normal session proves the stock was not risk-warned; closing on the 5%
   limit supports it.  Risk intervals are trimmed, split or dropped to agree.

Every adjustment is logged so the result can be audited.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .symbols import risk_status_from_name

MAIN_BOARD_RISK_LIMIT_CHANGE = "2026-07-06"  # 5% -> 10% for main-board risk-warned stocks
SSE_COVERAGE_START = "2013-01-01"
BSE_COVERAGE_START = "2021-11-15"

INTERVAL_COLUMNS = [
    "symbol", "status", "start_date", "end_date", "method", "source", "start_is_lower_bound",
    "start_title", "end_title", "run_id", "schema_version",
]


# ---------------------------------------------------------------------------
# Calendar / bars context
# ---------------------------------------------------------------------------


@dataclass
class HistoryContext:
    """Sessions (ISO dates, full calendar) and per-symbol bar dates."""

    sessions: list[str]
    bar_dates: dict[str, list[str]] = field(default_factory=dict)

    def on_or_after(self, day: str) -> str:
        return self.sessions[min(bisect.bisect_left(self.sessions, day), len(self.sessions) - 1)]

    def next(self, day: str) -> str:
        return self.sessions[min(bisect.bisect_right(self.sessions, day), len(self.sessions) - 1)]

    def prev(self, day: str) -> str:
        return self.sessions[max(bisect.bisect_left(self.sessions, day) - 1, 0)]

    def between(self, a: str, b: str) -> int:
        """Sessions strictly between ``a`` and ``b``."""
        return bisect.bisect_left(self.sessions, b) - bisect.bisect_right(self.sessions, a)

    def bars(self, symbol: str) -> list[str] | None:
        dates = self.bar_dates.get(symbol)
        return dates if dates else None


def _status_code(name: object) -> str | None:
    """'*ST' | 'ST' | 'DL' | None for a short name."""
    status = risk_status_from_name(name) if isinstance(name, str) and name.strip() else None
    return "DL" if status == "DELISTING" else status


# ---------------------------------------------------------------------------
# Title classification
# ---------------------------------------------------------------------------

IGNORE = ["可能", "进展", "申请", "风险提示", "存在被", "相关事项", "监管工作函", "问询函", "回复", "补充", "更正",
          "摘要", "更新后", "说明", "意见", "核查", "自查", "会计师", "律师", "专项", "独立董事", "撤回", "取消",
          "整改", "情况报告", "年度报告", "终止上市", "触及", "暂不", "不提交", "不申请", "无需", "关注", "中诚信",
          "评级", "债券", "受托管理", "上海证券交易所关于", "筹划"]


def _normalise_title(title: str) -> str:
    text = re.sub(r"\s+", "", title or "")
    for old, new in (("（", "("), ("）", ")"), ("“", ""), ("”", ""), ('"', ""), ("'", "")):
        text = text.replace(old, new)
    text = re.sub(r"^\[[^\]]*\]", "", text)  # BSE '[临时公告]'
    text = text.replace("其他退市风险警示", "其他风险警示")  # typo seen on SSE
    text = re.sub(r"其他风险(?!警示)", "其他风险警示", text)  # '实施其他风险的公告'
    return text.replace("风险风险警示", "风险警示")


def classify_title(title: str, exchange: str = "SSE") -> dict[str, str | None]:
    """Flag actions for 退市风险警示 (``star``) and 其他风险警示 (``st``).

    Actions: 'set' | 'revoke' | 'revoke_implied' | 'assert' | None.
    ``kind``: 'change' (status may change), 'assert' (继续…) or 'ignore'.
    """
    text = _normalise_title(title)
    ignore = {"star": None, "st": None, "kind": "ignore"}
    if "风险警示" not in text or any(word in text for word in IGNORE):
        return ignore
    # SSE pre-warnings ('将被实施退市风险警示的第X次提示性公告') are not the notice itself;
    # on BSE '将被实施退市风险警示的(提示性)公告' is the implementation notice.
    if exchange == "SSE" and re.search(r"将被|将实施|预计", text) and (
        re.search(r"第.次", text) or ("提示" in text and "停牌" not in text)
    ):
        return ignore
    body = text.split("关于", 1)[1] if "关于" in text else text
    body = re.sub(r"的(补充)?(提示性)?公告.*$", "", body)
    body = re.sub(r"(?<=风险警示)(?=继续|实施|叠加)", "暨", body)
    clauses = re.split(
        r"暨|并|，|,|、|(?:及|和|同时)(?=(?:公司)?(?:股票)?(?:交易)?(?:将)?(?:被)?(?:撤销|实施|实行|继续|叠加|变更))", body
    )
    action: dict[str, str | None] = {"star": None, "st": None}
    for clause in clauses:
        if not clause or "风险警示" not in clause:
            continue
        has_star, has_st = "退市风险警示" in clause, "其他风险警示" in clause
        generic = not has_star and not has_st  # '撤销风险警示' / '撤销相关风险警示'
        if "撤销" in clause:
            if (has_star and "因重整而被实施的退市风险警示" not in clause) or generic:
                action["star"] = "revoke"
            if (has_st and not re.search(r"部分(撤销)?其他风险警示", clause)) or generic:
                action["st"] = "revoke"
        elif "继续" in clause:
            if has_star:
                action["star"] = "assert"
            if has_st:
                action["st"] = "assert"
        elif re.search(r"实施|实行|叠加|对公司股票", clause):
            if has_star:
                action["star"] = "set"
            if has_st:
                action["st"] = "set"
    if action["star"] is None and action["st"] is None:
        return ignore
    if action["star"] == "revoke" and action["st"] is None:
        action["st"] = "revoke_implied"  # back to a normal name unless ST is said to continue
    action["kind"] = "change" if any(v in ("set", "revoke", "revoke_implied") for v in action.values()) else "assert"
    return action


def events_from_bulletins(bulletins: pd.DataFrame, exchange: str) -> pd.DataFrame:
    """bulletins: symbol, pub_date, title, bulletin_id -> classified events."""
    rows = []
    for row in bulletins.itertuples(index=False):
        action = classify_title(row.title, exchange)
        if action["kind"] != "ignore":
            rows.append({"symbol": row.symbol, "pub_date": row.pub_date, "title": row.title,
                         "bid": str(row.bulletin_id), "star": action["star"], "st": action["st"],
                         "kind": action["kind"]})
    return pd.DataFrame(rows, columns=["symbol", "pub_date", "title", "bid", "star", "st", "kind"])


# ---------------------------------------------------------------------------
# Effective dates and transitions
# ---------------------------------------------------------------------------


def _suspension_day(ctx: HistoryContext, pub_date: str, exchange: str) -> str:
    return ctx.on_or_after(pub_date) if exchange == "SSE" else ctx.next(pub_date)


def effective_date(ctx: HistoryContext, symbol: str, pub_date: str, title: str, exchange: str) -> tuple[str, str]:
    """First session under the new status, and how it was determined."""
    d0 = _suspension_day(ctx, pub_date, exchange)
    dates = ctx.bars(symbol)
    resume = "复牌" in title
    if dates and dates[0] <= d0 <= dates[-1]:
        if d0 not in set(dates):
            return dates[bisect.bisect_right(dates, d0)], "bars:first_bar_after_suspension"
        if resume:
            return d0, "bars:resumption_day"
        return ctx.next(d0), "bars:no_gap_assume_next_session"
    if resume:
        return d0, "calendar:resumption_day"
    return ctx.next(d0), "calendar:assume_1day_suspension"


def _gap_score(ctx: HistoryContext, symbol: str, pub_date: str, title: str, exchange: str) -> int:
    d0 = _suspension_day(ctx, pub_date, exchange)
    dates = ctx.bars(symbol)
    if dates and dates[0] <= d0 <= dates[-1]:
        return 3 if (d0 not in set(dates) or "复牌" in title) else 0
    return 2 if ("停牌" in title or "复牌" in title) else 1


def _status_of(star: int, st: int) -> str | None:
    return "*ST" if star else ("ST" if st else None)


def _apply(flags: dict[str, int], event: dict) -> dict[str, int]:
    new = dict(flags)
    if event.get("kind") == "override":
        status = event["override_status"]
        return {"star": 1, "st": flags["st"]} if status == "*ST" else {"star": 0, "st": int(status == "ST")}
    for key in ("star", "st"):
        value = event[key]
        if value in ("set", "assert"):
            new[key] = 1
        elif value in ("revoke", "revoke_implied"):
            new[key] = 0
    return new


def build_transitions(
    ctx: HistoryContext,
    events: pd.DataFrame,
    exchange: str,
    overrides: list[dict] | None = None,
    initial: dict[str, tuple[str | None, str, str]] | None = None,
    duplicate_window_days: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-symbol status transitions; the first row per symbol (``eff_date``
    None) is the state before the first observed event."""
    overrides = overrides or []
    initial = initial or {}
    transitions, issues = [], []
    symbols = sorted(set(events["symbol"]) | {o["symbol"] for o in overrides} | set(initial))
    for symbol in symbols:
        rows = events[events["symbol"] == symbol].sort_values(["pub_date", "bid"]).to_dict("records")
        for item in (o for o in overrides if o["symbol"] == symbol):
            rows.append({"symbol": symbol, "pub_date": item["eff_date"], "title": item["title"], "bid": "~",
                         "star": None, "st": None, "kind": "override", "override_status": item["status"],
                         "override_method": item["method"]})
        rows.sort(key=lambda r: (r["pub_date"], r["bid"]))
        flags = {}
        for key in ("star", "st"):
            first = next(((n, r[key]) for n, r in enumerate(rows) if r.get(key) not in (None, "revoke_implied")),
                         (None, None))
            # A flag starts on only if its first mention revokes it, or the very
            # first event asserts it.
            flags[key] = 1 if (first[1] == "revoke" or (first[1] == "assert" and first[0] == 0)) else 0
        method0, title0 = "initial_from_first_event", "<state before first observed event>"
        if symbol in initial:
            status, method0, title0 = initial[symbol]
            flags = {"star": int(status == "*ST"), "st": int(status == "ST")}
        current = _status_of(**flags)
        transitions.append({"symbol": symbol, "eff_date": None, "status": current, "pub_date": None,
                            "title": title0, "method": method0})
        i = 0
        while i < len(rows):
            row = rows[i]
            if row["kind"] == "override":
                flags = _apply(flags, row)
                status = _status_of(**flags)
                if status != current:
                    transitions.append({"symbol": symbol, "eff_date": row["pub_date"], "status": status,
                                        "pub_date": None, "title": row["title"], "method": row["override_method"]})
                    current = status
                i += 1
                continue
            for key in ("star", "st"):
                if row[key] == "assert" and flags[key] == 0:
                    issues.append({"symbol": symbol, "pub_date": row["pub_date"], "title": row["title"],
                                   "issue": f"assert_{key}_while_off", "implied_status": _status_of(**_apply(flags, row))})
                if row[key] == "revoke" and flags[key] == 0:
                    implied = dict(flags)
                    implied[key] = 1
                    issues.append({"symbol": symbol, "pub_date": row["pub_date"], "title": row["title"],
                                   "issue": f"revoke_{key}_while_off", "implied_status": _status_of(**implied)})
            new = _apply(flags, row)
            status = _status_of(**new)
            if status == current:
                flags = new
                i += 1
                continue
            candidates, j = [i], i + 1
            limit = (date.fromisoformat(row["pub_date"]) + timedelta(days=duplicate_window_days)).isoformat()
            while j < len(rows) and rows[j]["pub_date"] <= limit and rows[j]["kind"] != "override":
                if rows[j]["kind"] == "change":
                    if _status_of(**_apply(flags, rows[j])) != status:
                        break
                    candidates.append(j)
                j += 1
            best = max(candidates, key=lambda c: (_gap_score(ctx, symbol, rows[c]["pub_date"], rows[c]["title"],
                                                             exchange), -c))
            chosen = rows[best]
            eff, method = effective_date(ctx, symbol, chosen["pub_date"], chosen["title"], exchange)
            if len(candidates) > 1:
                method += f"|duplicates({len(candidates)})"
            transitions.append({"symbol": symbol, "eff_date": eff, "status": status, "pub_date": chosen["pub_date"],
                                "title": chosen["title"], "method": method})
            current = status
            for c in candidates:
                flags = _apply(flags, rows[c])
            i = max(candidates) + 1
    columns = ["symbol", "eff_date", "status", "pub_date", "title", "method"]
    return (pd.DataFrame(transitions, columns=columns),
            pd.DataFrame(issues, columns=["symbol", "pub_date", "title", "issue", "implied_status"]))


def _normalise(transitions: pd.DataFrame) -> pd.DataFrame:
    frame = transitions.copy()
    frame["_key"] = frame["eff_date"].fillna("0000")
    frame = frame.sort_values(["symbol", "_key", "terminal"], kind="stable").drop(columns="_key")
    kept = []
    for _, group in frame.groupby("symbol", sort=False):
        previous, dead = object(), False
        for row in group.to_dict("records"):
            if dead or (row["status"] == previous and not row["terminal"]):
                continue
            if previous == "DL" and not row["terminal"] and row["status"] != "DL":
                continue  # nothing but delisting follows a delisting period
            kept.append(row)
            previous, dead = row["status"], row["terminal"]
    return pd.DataFrame(kept, columns=list(frame.columns)).reset_index(drop=True)


def add_delisting(
    ctx: HistoryContext, transitions: pd.DataFrame, notices: pd.DataFrame, delist_dates: dict[str, str],
    exchange: str,
) -> pd.DataFrame:
    """Delisting-period ('DL') transitions from '进入退市整理期' notices, and a
    terminal row at the exchange delisting date."""
    added = []
    for row in notices.itertuples(index=False):
        d0 = _suspension_day(ctx, row.pub_date, exchange)
        dates = ctx.bars(row.symbol)
        later = [d for d in (dates or []) if d >= d0]
        eff, method = (later[0], "bars:first_bar_after_delisting_period_notice") if later else (
            ctx.next(d0), "calendar:notice_next_session")
        added.append({"symbol": row.symbol, "eff_date": eff, "status": "DL", "pub_date": row.pub_date,
                      "title": row.title, "method": method, "terminal": False})
    for symbol, day in delist_dates.items():
        added.append({"symbol": symbol, "eff_date": day, "status": None, "pub_date": None,
                      "title": "<delisted>", "method": "exchange_delist_date", "terminal": True})
    frame = pd.concat([transitions.assign(terminal=False), pd.DataFrame(added)], ignore_index=True)
    frame["terminal"] = frame["terminal"].fillna(False).astype(bool)
    return _normalise(frame)


# ---------------------------------------------------------------------------
# Name evidence
# ---------------------------------------------------------------------------

_PREFIX = re.compile(
    r"^\s*(S?\*?ST[一-龥A-Za-z0-9]{1,6}?)(?=关于|：|:|\s|股票|公司|20\d\d|第|重大|对|风险|董事|监事|年|独立|收到|澄清|"
    r"更正|补充|股东|日常|控股|重整|诉讼|资产|终止|全资|子公司|业绩|$)"
)


def name_evidence(bulletins: pd.DataFrame, exchange: str, change_days: set[tuple[str, str]]) -> pd.DataFrame:
    """Dated names from bulletins, excluding everything a company published on
    the day of a status-change notice (the notice and the documents released
    with it still carry the name from before the change)."""
    rows = []
    for row in bulletins.itertuples(index=False):
        if (row.symbol, row.pub_date) in change_days:
            continue
        name = row.security_name if isinstance(row.security_name, str) else None
        if name and name.strip():
            rows.append({"symbol": row.symbol, "date": row.pub_date, "name": name, "status": _status_code(name),
                         "ev": "security_name" if exchange == "SSE" else "company_name"})
        if exchange == "SSE":
            match = _PREFIX.match(row.title or "")
            if match:
                rows.append({"symbol": row.symbol, "date": row.pub_date, "name": match.group(1),
                             "status": _status_code(match.group(1)), "ev": "title_prefix"})
    return pd.DataFrame(rows, columns=["symbol", "date", "name", "status", "ev"])


def _model(transitions: pd.DataFrame):
    index = {}
    for symbol, group in transitions.groupby("symbol", sort=False):
        start = group["status"].iloc[0] if not isinstance(group["eff_date"].iloc[0], str) else None
        dated = group[group["eff_date"].map(lambda v: isinstance(v, str))]
        index[symbol] = (start, dated["eff_date"].tolist(), dated["status"].tolist())

    def status(symbol: str, day: str) -> str | None:
        if symbol not in index:
            return None
        start, days, states = index[symbol]
        k = bisect.bisect_right(days, day)
        return states[k - 1] if k else start

    return status


def evidence_mismatches(ctx: HistoryContext, transitions: pd.DataFrame, evidence: pd.DataFrame,
                        tolerance: int = 2) -> pd.DataFrame:
    """Evidence dated s agrees if it matches the model on the session before
    the first session on/after s, or within ``tolerance`` sessions after."""
    status = _model(transitions)
    rows = []
    for row in evidence.itertuples(index=False):
        d0 = ctx.on_or_after(row.date)
        days = [ctx.prev(d0), d0]
        for _ in range(tolerance):
            days.append(ctx.next(days[-1]))
        states = [status(row.symbol, day) for day in days]
        agrees = row.status in states or (row.status == "DL" and "*ST" in states) or (row.status == "*ST" and "DL" in states)
        if not agrees:
            rows.append({"symbol": row.symbol, "date": row.date, "name": row.name, "ev": row.ev,
                         "ev_status": row.status, "model": states[0]})
    return pd.DataFrame(rows, columns=["symbol", "date", "name", "ev", "ev_status", "model"])


def repair_with_evidence(
    ctx: HistoryContext, events: pd.DataFrame, evidence: pd.DataFrame, exchange: str, notices: pd.DataFrame,
    delist_dates: dict[str, str], max_iterations: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Add overrides until the transitions agree with dated name evidence.

    A contradiction needs support: two evidence dates, a title prefix, or an
    event that implies a prior state.  The corrected change date is taken
    from a notice in the uncertain window, else the resumption after the
    longest suspension in it, else the window's upper bound.
    """
    overrides: list[dict] = []
    initial: dict[str, tuple[str | None, str, str]] = {}
    log: list[dict] = []
    tried: set[tuple] = set()
    transitions = pd.DataFrame()
    mismatches = pd.DataFrame()
    for _ in range(max_iterations):
        transitions, issues = build_transitions(ctx, events, exchange, overrides, initial)
        transitions = add_delisting(ctx, transitions, notices, delist_dates, exchange)
        implied = issues[issues["implied_status"].notna()].rename(columns={"pub_date": "date",
                                                                            "implied_status": "status"})
        implied = implied.assign(name=implied["issue"], ev="implied_by_event")[["symbol", "date", "name", "status", "ev"]]
        # The implied state holds on the session before the event.
        implied["date"] = implied["date"].map(lambda d: ctx.prev(ctx.on_or_after(d)))
        all_evidence = pd.concat([evidence, implied], ignore_index=True)
        mismatches = evidence_mismatches(ctx, transitions, all_evidence)
        status = _model(transitions)
        added = 0
        for symbol, group in mismatches.sort_values("date").groupby("symbol"):
            first = group.iloc[0]
            key = (symbol, first["date"], first["ev_status"])
            if key in tried:
                continue
            tried.add(key)
            support = group[group["ev_status"] == first["ev_status"]]
            strong = (first["ev"] == "implied_by_event" or (first["ev"] == "title_prefix" and first["ev_status"])
                      or support["date"].nunique() >= 2)
            if not strong:
                log.append({"symbol": symbol, "date": first["date"], "name": first["name"],
                            "status": first["ev_status"], "action": "unrepaired_single_evidence"})
                continue
            d0 = ctx.on_or_after(first["date"])
            rows = transitions[transitions["symbol"] == symbol]
            dated = rows[rows["eff_date"].map(lambda v: isinstance(v, str)) & (rows["eff_date"] <= d0)]
            if len(dated) and dated["terminal"].iloc[-1]:
                log.append({"symbol": symbol, "date": first["date"], "name": first["name"],
                            "status": first["ev_status"], "action": "evidence_after_delisting_ignored"})
                continue
            segment = status(symbol, ctx.prev(d0))
            segment_start = dated["eff_date"].iloc[-1] if len(dated) else None
            earlier = all_evidence[(all_evidence["symbol"] == symbol) & (all_evidence["date"] < first["date"])]
            agree = earlier[earlier["status"].map(lambda s: s == segment)
                            & ((earlier["date"] >= segment_start) if segment_start else True)]
            if segment_start is None and agree.empty:
                initial[symbol] = (first["ev_status"], f"initial_from_name_evidence({first['ev']})",
                                   f"<name evidence {first['date']} {first['name']}>")
                log.append({"symbol": symbol, "date": first["date"], "name": first["name"],
                            "status": first["ev_status"], "action": "initial_state_from_evidence"})
                added += 1
                continue
            low = max([segment_start or "1990-01-01"] + agree["date"].tolist())
            high = d0 if first["ev"] != "implied_by_event" else first["date"]
            candidate = None
            window = events[(events["symbol"] == symbol) & (events["pub_date"] > low) & (events["pub_date"] <= high)]
            if first["ev"] == "implied_by_event":
                window = window[window["pub_date"] < high]
            for row in window.itertuples(index=False):
                eff, method = effective_date(ctx, symbol, row.pub_date, row.title, exchange)
                if eff > low:
                    candidate = (eff, "repair:reinterpreted_event|" + method, row.title)
                    break
            if candidate is None:
                dates = ctx.bars(symbol) or []
                best = None
                for k in range(1, len(dates)):
                    if low < dates[k] <= high:
                        gap = ctx.between(dates[k - 1], dates[k])
                        if gap >= 2 and (best is None or gap > best[1]):
                            best = (dates[k], gap)
                if best:
                    candidate = (best[0], f"repair:resumption_after_gap({best[1]})", None)
            if candidate is None:
                candidate = (high, "repair:evidence_upper_bound", None)
            overrides.append({"symbol": symbol, "eff_date": candidate[0], "status": first["ev_status"],
                              "method": candidate[1] + f"|low={low}|high={high}",
                              "title": candidate[2] or f"<name evidence {first['date']} {first['name']} ({first['ev']})>"})
            log.append({"symbol": symbol, "date": first["date"], "name": first["name"],
                        "status": first["ev_status"], "action": candidate[1].split("|")[0], "eff": candidate[0],
                        "low": low, "high": high})
            added += 1
        if not added:
            break
    return transitions, pd.DataFrame(log), mismatches


# ---------------------------------------------------------------------------
# Intervals and price-limit reconciliation
# ---------------------------------------------------------------------------


def transitions_to_intervals(transitions: pd.DataFrame, source: str, coverage_start: str) -> pd.DataFrame:
    rows = []
    for symbol, group in transitions.groupby("symbol", sort=False):
        group = group.reset_index(drop=True)
        for i, row in group.iterrows():
            if not isinstance(row["status"], str):
                continue
            following = group.iloc[i + 1] if i + 1 < len(group) else None
            rows.append({
                "symbol": symbol,
                "status": "DELISTING" if row["status"] == "DL" else row["status"],
                "start_date": row["eff_date"] if isinstance(row["eff_date"], str) else coverage_start,
                "end_date": following["eff_date"] if following is not None else None,
                "method": row["method"],
                "source": source,
                "start_is_lower_bound": not isinstance(row["eff_date"], str),
                "start_title": row["title"],
                "end_title": following["title"] if following is not None else None,
            })
    return pd.DataFrame(rows, columns=INTERVAL_COLUMNS[:-2])


def reconcile_with_prices(
    intervals: pd.DataFrame, evidence: pd.DataFrame, sessions: list[str], data_start: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Make SSE main-board risk intervals agree with 5%-limit price evidence.

    First, an interval whose start is only an upper bound from name evidence
    (``repair:evidence_upper_bound|low=…``) is extended back to just after
    the last band break in the uncertain window (or to ``data_start`` if
    there is none), provided the extension contains support.

    ``evidence``: symbol, trade_date, beyond (traded outside the 5% band on
    a normal session), at_limit (closed on the 5% limit price).  Support is
    a close on the limit *without* trading beyond the band that day; one
    side with at most two support days is treated as coincidence.  For each
    ST/*ST interval with ``beyond`` days inside:
      * support only after the last beyond day -> start moves after it;
      * support only before the first beyond day -> end moves to it;
      * no support at all -> the interval is dropped;
      * support on both sides -> split around the beyond days (flagged).
    """
    if evidence.empty or intervals.empty:
        return intervals, pd.DataFrame(columns=["symbol", "start_date", "end_date", "action", "detail"])
    by_symbol = {symbol: group for symbol, group in evidence.groupby("symbol")}
    kept, log = [], []
    for row in intervals.to_dict("records"):
        group = by_symbol.get(row["symbol"])
        if row["status"] not in ("ST", "*ST") or group is None:
            kept.append(row)
            continue
        low_match = re.search(r"repair:evidence_upper_bound\|low=(\d{4}-\d{2}-\d{2})", row["method"] or "")
        if low_match:
            low = low_match.group(1)
            window = group[(group["trade_date"] > low) & (group["trade_date"] < row["start_date"])]
            breaks = window.loc[window["beyond"], "trade_date"]
            earliest = sessions[min(bisect.bisect_right(sessions, low), len(sessions) - 1)]
            if data_start is not None:
                earliest = max(earliest, data_start)
            new_start = (sessions[min(bisect.bisect_right(sessions, breaks.max()), len(sessions) - 1)]
                         if not breaks.empty else earliest)
            extension = window[(window["trade_date"] >= new_start) & window["at_limit"] & ~window["beyond"]]
            if new_start < row["start_date"] and not extension.empty:
                log.append({**_keys(row), "action": "start_extended",
                            "detail": f"support={len(extension)} in [{new_start}..{row['start_date']})"})
                row = {**row, "start_date": new_start, "method": row["method"] + "|price:start_extended"}
        end = row["end_date"] or "9999-12-31"
        inside = group[(group["trade_date"] >= row["start_date"]) & (group["trade_date"] < end)]
        beyond = inside.loc[inside["beyond"], "trade_date"]
        if beyond.empty:
            kept.append(row)
            continue
        support = inside.loc[inside["at_limit"] & ~inside["beyond"], "trade_date"]
        first_beyond, last_beyond = beyond.min(), beyond.max()
        before = int((support < first_beyond).sum())
        after_count = int((support > last_beyond).sum())
        detail = (f"beyond={len(beyond)} [{first_beyond}..{last_beyond}] "
                  f"support_before={before} support_after={after_count}")
        after = sessions[min(bisect.bisect_right(sessions, last_beyond), len(sessions) - 1)]
        strong_before, strong_after = before >= 3, after_count >= 3
        if strong_before and strong_after:
            decision = "split_unresolved"
        elif strong_after or (not strong_before and after_count > before):
            decision = "start_moved"
        elif strong_before or before > after_count:
            decision = "end_moved"
        else:
            decision = "dropped"
        if decision == "dropped":
            log.append({**_keys(row), "action": "dropped", "detail": detail})
        elif decision == "start_moved":
            log.append({**_keys(row), "action": "start_moved", "detail": detail + f" new_start={after}"})
            kept.append({**row, "start_date": after, "start_is_lower_bound": False,
                         "method": row["method"] + "|price:start_after_band_break"})
        elif decision == "end_moved":
            log.append({**_keys(row), "action": "end_moved", "detail": detail + f" new_end={first_beyond}"})
            kept.append({**row, "end_date": first_beyond, "method": row["method"] + "|price:end_at_band_break"})
        else:
            log.append({**_keys(row), "action": "split_unresolved", "detail": detail})
            kept.append({**row, "end_date": first_beyond, "method": row["method"] + "|price:split"})
            kept.append({**row, "start_date": after, "start_is_lower_bound": False,
                         "method": row["method"] + "|price:split"})
    return pd.DataFrame(kept, columns=intervals.columns), pd.DataFrame(log)


def _keys(row: dict) -> dict:
    return {"symbol": row["symbol"], "start_date": row["start_date"], "end_date": row["end_date"],
            "status": row["status"]}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class RiskHistory:
    intervals: pd.DataFrame
    log: pd.DataFrame
    mismatches: pd.DataFrame
    summary: dict[str, object]


def build_exchange_history(
    ctx: HistoryContext,
    bulletins: pd.DataFrame,
    exchange: str,
    delist_dates: dict[str, str],
    price_evidence: pd.DataFrame | None = None,
) -> RiskHistory:
    """bulletins: exchange, symbol, pub_date, title, security_name, bulletin_id."""
    frame = bulletins[bulletins["exchange"] == exchange].drop_duplicates("bulletin_id")
    frame = frame.sort_values(["symbol", "pub_date", "bulletin_id"], kind="stable")
    events = events_from_bulletins(frame, exchange)
    notice_pattern = r"进入退市整理期(?:交易)?的公告" if exchange == "SSE" else "将进入退市整理期"
    notices = frame[frame["title"].str.contains(notice_pattern, regex=True, na=False)
                    & ~frame["title"].str.contains("风险提示|第.次", regex=True, na=False)]
    notices = (notices.sort_values("pub_date").drop_duplicates("symbol")
               [["symbol", "pub_date", "title"]].reset_index(drop=True))
    symbols = set(events["symbol"]) | set(notices["symbol"])
    delist = {symbol: day for symbol, day in delist_dates.items() if symbol in symbols}
    changes = events[events["kind"] == "change"]
    change_days = set(zip(changes["symbol"], changes["pub_date"]))
    evidence = name_evidence(frame[frame["symbol"].isin(symbols)], exchange, change_days)
    transitions, log, mismatches = repair_with_evidence(ctx, events, evidence, exchange, notices, delist)
    source = "sse_bulletin" if exchange == "SSE" else "bse_announcement"
    coverage = SSE_COVERAGE_START if exchange == "SSE" else BSE_COVERAGE_START
    intervals = transitions_to_intervals(transitions, source, coverage)
    price_log = pd.DataFrame()
    if price_evidence is not None and exchange == "SSE":
        data_start = min((dates[0] for dates in ctx.bar_dates.values() if dates), default=None)
        intervals, price_log = reconcile_with_prices(intervals, price_evidence, ctx.sessions, data_start)
    combined_log = pd.concat([log.assign(stage="name_evidence"), price_log.assign(stage="price_limit")],
                             ignore_index=True)
    summary = {
        "bulletins": int(len(frame)),
        "events": int(len(events)),
        "symbols": int(len(symbols)),
        "intervals": int(len(intervals)),
        "name_repairs": int(len(log)),
        "price_adjustments": {str(k): int(v) for k, v in price_log["action"].value_counts().items()}
        if len(price_log) else {},
        "remaining_name_mismatches": int(len(mismatches)),
    }
    return RiskHistory(intervals, combined_log, mismatches, summary)


def to_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return pd.Timestamp(value).date()


def sse_main_price_evidence(root, sessions: list[str], symbols: list[str]) -> pd.DataFrame:
    """Normal-session 5%-band evidence for SSE main-board ``symbols``.

    A session is normal when the previous session also traded (no
    resumption) and the stock has more than five bars (no IPO window).
    Returns only sessions that trade beyond the 5% band or close on it.
    """
    import duckdb

    if not symbols:
        return pd.DataFrame(columns=["symbol", "trade_date", "beyond", "at_limit"])
    canonical = (root / "data" / "canonical").resolve().as_posix()
    listed = ",".join(f"'{symbol}'" for symbol in symbols)
    calendar = pd.DataFrame({"trade_date": pd.to_datetime(pd.Series(sessions))})
    with duckdb.connect() as con:
        con.register("calendar_frame", calendar)
        frame = con.execute(
            f"""
            WITH cal AS (SELECT CAST(trade_date AS DATE) AS trade_date,
                                row_number() OVER (ORDER BY trade_date) AS idx FROM calendar_frame),
            b AS (SELECT symbol, trade_date, high, low, close
                  FROM read_parquet('{canonical}/daily_bars/**/*.parquet', union_by_name=true, hive_partitioning=false)
                  WHERE symbol IN ({listed}) AND trade_date < DATE '{MAIN_BOARD_RISK_LIMIT_CHANGE}'),
            f AS (SELECT symbol, effective_date, hfq_factor
                  FROM read_parquet('{canonical}/adjustment_factors/**/*.parquet', union_by_name=true, hive_partitioning=false)
                  WHERE symbol IN ({listed})),
            j AS (SELECT b.*, f.hfq_factor, cal.idx FROM b
                  ASOF LEFT JOIN f ON b.symbol = f.symbol AND b.trade_date >= f.effective_date
                  JOIN cal ON cal.trade_date = b.trade_date),
            x AS (SELECT *, lag(close) OVER w AS prev_close, lag(hfq_factor) OVER w AS prev_hfq,
                         lag(idx) OVER w AS prev_idx, row_number() OVER w AS rn
                  FROM j WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)),
            y AS (SELECT symbol, trade_date, high, low, close,
                         prev_close * COALESCE(prev_hfq / hfq_factor, 1.0) AS ref
                  FROM x WHERE rn > 5 AND idx = prev_idx + 1),
            z AS (SELECT *, floor(ref * 1.05 * 100 + 0.5 + 1e-6) / 100 AS up5,
                            floor(ref * 0.95 * 100 + 0.5 + 1e-6) / 100 AS down5 FROM y)
            SELECT symbol, strftime(trade_date, '%Y-%m-%d') AS trade_date,
                   (high > up5 + 0.005 OR low < down5 - 0.005) AS beyond,
                   (abs(close - up5) < 0.005 OR abs(close - down5) < 0.005) AS at_limit
            FROM z
            WHERE high > up5 + 0.005 OR low < down5 - 0.005
               OR abs(close - up5) < 0.005 OR abs(close - down5) < 0.005
            ORDER BY symbol, trade_date
            """
        ).fetchdf()
    return frame


# ---------------------------------------------------------------------------
# Bulletin normalization
# ---------------------------------------------------------------------------

BULLETIN_COLUMNS = ["exchange", "symbol", "pub_date", "title", "security_name", "bulletin_id", "keyword",
                    "run_id", "schema_version"]


def normalize_sse_bulletins(raw: pd.DataFrame, keyword: str, run_id: str, schema_version: str) -> pd.DataFrame:
    """SSE bulletins keyed by file URL: ``ORG_BULLETIN_ID`` is shared by every
    file of one disclosure package (e.g. the 暨停牌 notice and the board's
    statements published with it), so it cannot identify a notice."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=BULLETIN_COLUMNS)
    package = raw["ORG_BULLETIN_ID"].astype(str)
    url = raw["URL"].astype("string").str.strip() if "URL" in raw.columns else pd.Series(pd.NA, index=raw.index)
    frame = pd.DataFrame({
        "exchange": "SSE",
        "symbol": raw["SECURITY_CODE"].astype(str).str.strip(),
        "pub_date": pd.to_datetime(raw["SSEDATE"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "title": raw["TITLE"].astype(str),
        "security_name": raw["SECURITY_NAME"] if "SECURITY_NAME" in raw.columns else None,
        "bulletin_id": url.where(url.notna() & (url != ""), package).astype(str),
    })
    frame["keyword"], frame["run_id"], frame["schema_version"] = keyword, run_id, schema_version
    frame = frame[frame["symbol"].str.match(r"^6\d{5}$") & frame["pub_date"].notna()]
    return frame[BULLETIN_COLUMNS].reset_index(drop=True)


def _base_name(name: object) -> str:
    from .symbols import clean_name

    text = clean_name(name).upper()
    text = re.sub(r"^(?:S?\*?ST)", "", text)
    return text[:-1] if text.endswith("退") else text


def normalize_bse_announcements(
    raw: pd.DataFrame, keyword: str, run_id: str, schema_version: str, master: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """BSE announcements, with pre-2025 codes (43/83/87) mapped to 920xxx.

    A code is mapped only when ``920`` + its last three digits exists in the
    master under the same company base name; the query also returns NEEQ
    companies, which are dropped.  Returns (frame, unmapped count).
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=BULLETIN_COLUMNS), 0
    bse = master[master["exchange"] == "BSE"]
    names = {symbol: _base_name(name) for symbol, name in zip(bse["symbol"], bse["name"])}

    def mapped(code: str, company: str) -> str | None:
        code = str(code).strip()
        candidate = code if code.startswith("920") else "920" + code[-3:]
        if candidate not in names:
            return None
        return candidate if code.startswith("920") or names[candidate] == _base_name(company) else None

    symbols = [mapped(code, company) for code, company in zip(raw["companyCd"], raw["companyName"])]
    frame = pd.DataFrame({
        "exchange": "BSE",
        "symbol": symbols,
        "pub_date": pd.to_datetime(raw["publishDate"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "title": raw["disclosureTitle"].astype(str),
        "security_name": raw["companyName"],
        "bulletin_id": raw["destFilePath"].astype(str),
    })
    frame["keyword"], frame["run_id"], frame["schema_version"] = keyword, run_id, schema_version
    unmapped = int(frame["symbol"].isna().sum())
    frame = frame[frame["symbol"].notna() & frame["pub_date"].notna()]
    return frame[BULLETIN_COLUMNS].reset_index(drop=True), unmapped


def merge_bulletins(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    parts = [part for part in (existing, incoming) if part is not None and not part.empty]
    if not parts:
        return pd.DataFrame(columns=BULLETIN_COLUMNS)
    frame = pd.concat([part.reindex(columns=BULLETIN_COLUMNS) for part in parts], ignore_index=True)
    frame = frame.drop_duplicates(["exchange", "bulletin_id"], keep="last")
    return frame.sort_values(["exchange", "pub_date", "symbol", "bulletin_id"], kind="stable", ignore_index=True)


# ---------------------------------------------------------------------------
# Windows and the combined dataset
# ---------------------------------------------------------------------------

RISK_KEYWORDS = {"风险警示": "risk_warning", "退市整理": "delisting_period"}
REFRESH_DAYS = 120  # windows ending this recently are re-fetched (late postings)


def quarter_windows(start: str, end: str) -> list[tuple[str, str]]:
    windows = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        for first, last in (("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30"), ("10-01", "12-31")):
            a, b = f"{year}-{first}", f"{year}-{last}"
            if b >= start and a <= end:
                windows.append((max(a, start), min(b, end)))
    return windows


def year_windows(start: str, end: str) -> list[tuple[str, str]]:
    return [(max(f"{year}-01-01", start), min(f"{year}-12-31", end))
            for year in range(int(start[:4]), int(end[:4]) + 1)]


def bulletin_windows(today: str, log: pd.DataFrame | None) -> list[tuple[str, str, str, str]]:
    """(exchange, keyword, start, end) still to fetch: never-fetched windows
    plus recent ones, which are refreshed for late postings."""
    done = set()
    if log is not None and not log.empty:
        done = set(zip(log["exchange"], log["keyword"], log["window_start"], log["window_end"]))
    refresh_from = (date.fromisoformat(today) - timedelta(days=REFRESH_DAYS)).isoformat()
    plan = []
    for keyword in RISK_KEYWORDS:
        for exchange, windows in (("SSE", quarter_windows(SSE_COVERAGE_START, today)),
                                  ("BSE", year_windows(BSE_COVERAGE_START, today))):
            for start, end in windows:
                if end >= refresh_from or (exchange, keyword, start, end) not in done:
                    plan.append((exchange, keyword, start, end))
    return plan


@dataclass
class CombinedRisk:
    intervals: pd.DataFrame
    adjustments: pd.DataFrame
    summary: dict[str, object]
    uncovered: list[str]


def derive_risk_intervals(
    root,
    sessions: list[str],
    master: pd.DataFrame,
    sz_changes: pd.DataFrame | None,
    bulletins: pd.DataFrame | None,
    run_id: str,
    schema_version: str,
    as_of: str,
) -> CombinedRisk:
    """SZSE intervals from dated name changes, SSE/BSE from bulletins, plus a
    current-name fallback for live risk-warned names nothing else covers."""
    from .normalization import NAME_CHANGE_COLUMNS, risk_intervals_from_names

    live = master[master["status"] != "delisted"]
    names = dict(zip(live["symbol"], live["name"]))
    exchange = dict(zip(master["symbol"], master["exchange"]))
    parts = []
    if sz_changes is not None and not sz_changes.empty:
        szse = {s: n for s, n in names.items() if exchange.get(s) == "SZSE"}
        sz = risk_intervals_from_names(sz_changes, szse, run_id, "szse_name_change")
        sz["source"] = "szse_name_change"
        sz["start_is_lower_bound"] = sz["start_date"].isna()
        parts.append(sz)
    adjustments, summary = [], {}
    if bulletins is not None and not bulletins.empty:
        symbols = sorted(set(bulletins["symbol"]))
        bar_dates = {}
        for symbol in symbols:
            path = root / "data" / "canonical" / "daily_bars" / f"symbol={symbol}" / "data.parquet"
            if path.exists():
                dates = pd.read_parquet(path, columns=["trade_date"])["trade_date"]
                bar_dates[symbol] = sorted(pd.to_datetime(dates).dt.strftime("%Y-%m-%d"))
        ctx = HistoryContext(sessions, bar_dates)
        delist = {s: pd.Timestamp(d).strftime("%Y-%m-%d") for s, d in zip(master["symbol"], master["delist_date"])
                  if d is not None and not pd.isna(d)}
        main = set(master.loc[master["board"] == "SSE_MAIN", "symbol"])
        evidence = sse_main_price_evidence(root, sessions, sorted(main & set(symbols)))
        for code in ("SSE", "BSE"):
            if (bulletins["exchange"] == code).any():
                history = build_exchange_history(ctx, bulletins, code, delist,
                                                 evidence if code == "SSE" else None)
                intervals = history.intervals.copy()
                for column in ("start_date", "end_date"):
                    intervals[column] = intervals[column].map(to_date).astype("object")
                parts.append(intervals)
                if not history.log.empty:
                    adjustments.append(history.log.assign(exchange=code))
                summary[code] = history.summary
    combined = pd.concat([part.reindex(columns=INTERVAL_COLUMNS) for part in parts], ignore_index=True) \
        if parts else pd.DataFrame(columns=INTERVAL_COLUMNS)
    # Live names showing a risk state that no dated interval covers today.
    day = pd.Timestamp(as_of).date()
    covered = set(combined.loc[
        combined["start_date"].map(lambda v: v is not None and not pd.isna(v) and v <= day)
        & combined["end_date"].map(lambda v: v is None or pd.isna(v) or v > day), "symbol"])
    empty = pd.DataFrame(columns=NAME_CHANGE_COLUMNS)
    fallback_names = {s: n for s, n in names.items()
                      if s not in covered and risk_status_from_name(n) in ("*ST", "ST", "DELISTING")}
    fallback = risk_intervals_from_names(empty, fallback_names, run_id, "current_name_only")
    fallback["source"], fallback["start_is_lower_bound"] = "current_name", True
    combined = pd.concat([combined, fallback.reindex(columns=INTERVAL_COLUMNS)], ignore_index=True)
    combined["run_id"], combined["schema_version"] = run_id, schema_version
    combined = combined.sort_values(["symbol", "start_date"], key=lambda s: s.astype(str), kind="stable",
                                    ignore_index=True)
    log = pd.concat(adjustments, ignore_index=True) if adjustments else pd.DataFrame(
        columns=["symbol", "action", "stage", "exchange"])
    for column in log.columns:
        log[column] = log[column].astype("string")
    summary["uncovered_live_risk_names"] = len(fallback_names)
    return CombinedRisk(combined, log, summary, sorted(fallback_names))
