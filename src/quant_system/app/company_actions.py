"""回购增持: buyback plans and holder increases/decreases (data_platform/company_actions.py) as lists,
an industry summary, and per-stock tags for the position manager.

Information only: research/buyback/buyback_study.py found no edge worth trading (WORKLOG_2026-10-07).
Shares of market cap use the latest close and total shares.  Computed once per catalog version.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from ..data_platform.company_actions import KINDS, PROGRESS
from .market import records

# Plans that never ran (未实施), stopped (已停止) or anything else unknown do not count as a company buying.
LIVE_PROGRESS = ("001", "002", "004", "006", "007")
TAG_DAYS = 90


class CompanyActionsMissing(RuntimeError):
    pass


class CompanyActions:
    def __init__(self, database: Path) -> None:
        self.database = database
        self._state: tuple[float, dict[str, Any] | CompanyActionsMissing] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.database.is_file():
            raise CompanyActionsMissing("行情库不存在")
        key = self.database.stat().st_mtime
        with self._lock:
            if self._state is None or self._state[0] != key:
                try:
                    self._state = (key, self._compute())
                except CompanyActionsMissing as exc:  # remembered: the board asks once per item
                    self._state = (key, exc)
            if isinstance(self._state[1], CompanyActionsMissing):
                raise self._state[1]
            return self._state[1]

    def _compute(self) -> dict[str, Any]:
        try:
            with duckdb.connect(str(self.database), read_only=True) as con:
                plans = con.execute("SELECT plan_id, symbol, name, notice_date, latest_notice_date, update_date, "
                                    "progress, kind, purpose, amount_lower, amount_upper, price_cap, start_date, "
                                    "end_date, done_amount, done_shares, done_avg_price, finish_date "
                                    "FROM buybacks").fetchdf()
                changes = con.execute("SELECT change_key, symbol, name, holder, direction, change_shares, "
                                      "change_pct_total, hold_pct_after, avg_price, channel, start_date, end_date, "
                                      "notice_date FROM holder_changes").fetchdf()
                last = con.execute("SELECT symbol, arg_max(close, trade_date) AS close, max(trade_date) AS day "
                                   "FROM daily_bars_adjusted GROUP BY symbol").fetchdf()
                shares = con.execute("SELECT symbol, arg_max(total_shares, change_date) AS total_shares "
                                     "FROM share_capital WHERE total_shares > 0 GROUP BY symbol").fetchdf()
                try:
                    industry = con.execute("SELECT symbol, arg_max(l1_name, start_date) AS industry "
                                           "FROM industry_sw GROUP BY symbol").fetchdf()
                except duckdb.CatalogException:
                    industry = pd.DataFrame(columns=["symbol", "industry"])
        except duckdb.CatalogException as exc:  # a catalog built before the company-actions step existed
            raise CompanyActionsMissing(str(exc)) from exc
        stock = last.merge(shares, on="symbol", how="left").merge(industry, on="symbol", how="left")
        stock["cap"] = stock["close"] * stock["total_shares"]
        stock = stock.set_index("symbol")
        for frame in (plans, changes):
            for column in [c for c in frame.columns if c.endswith("_date")]:
                frame[column] = pd.to_datetime(frame[column]).dt.date
        plans = plans.join(stock[["close", "cap", "industry"]], on="symbol")
        plans["plan_pct_lower"] = plans["amount_lower"] / plans["cap"] * 100
        plans["plan_pct_upper"] = plans["amount_upper"] / plans["cap"] * 100
        plans["done_pct"] = plans["done_amount"] / plans["cap"] * 100
        plans["close_vs_avg"] = plans["close"] / plans["done_avg_price"]
        plans["progress_name"] = plans["progress"].map(PROGRESS).fillna("其他")
        plans["kind_name"] = plans["kind"].map(KINDS)
        changes = changes.join(stock[["close", "industry"]], on="symbol")
        price = changes["avg_price"].where(changes["avg_price"] > 0, changes["close"])
        changes["amount"] = changes["change_shares"] * price
        as_of = max([d for d in (plans["latest_notice_date"].max() if len(plans) else None,
                                 changes["notice_date"].max() if len(changes) else None) if d is not None and d == d],
                    default=date.today())
        return {"plans": plans, "changes": changes, "as_of": as_of}

    # -- queries -------------------------------------------------------------

    def buybacks(self, days: int = 90, kind: str | None = None, progress: str | None = None,
                 industry: str | None = None, min_pct: float | None = None) -> dict[str, Any]:
        """Plans announced or updated in the last ``days``, newest first."""
        state = self._load()
        frame = state["plans"]
        frame = frame[frame["latest_notice_date"] >= state["as_of"] - timedelta(days=days)]
        if kind:
            frame = frame[frame["kind"] == kind]
        if progress:
            frame = frame[frame["progress"] == progress]
        if industry:
            frame = frame[frame["industry"] == industry]
        if min_pct is not None:
            frame = frame[frame["plan_pct_lower"].fillna(frame["done_pct"]) >= min_pct]
        frame = frame.sort_values(["latest_notice_date", "plan_pct_lower"], ascending=[False, False])
        columns = ["plan_id", "symbol", "name", "industry", "notice_date", "latest_notice_date", "progress",
                   "progress_name", "kind", "kind_name", "amount_lower", "amount_upper", "plan_pct_lower",
                   "plan_pct_upper", "price_cap", "start_date", "end_date", "done_amount", "done_pct",
                   "done_avg_price", "close", "close_vs_avg", "finish_date", "purpose"]
        return {"as_of": state["as_of"], "days": days, "total": len(frame), "rows": records(frame[columns]),
                "progress": PROGRESS, "kinds": KINDS}

    def holders(self, days: int = 90, direction: str | None = None, industry: str | None = None,
                min_pct: float | None = None) -> dict[str, Any]:
        """Holder increases (增持) and decreases (减持) announced in the last ``days``, newest first."""
        state = self._load()
        frame = state["changes"]
        frame = frame[frame["notice_date"] >= state["as_of"] - timedelta(days=days)]
        if direction:
            frame = frame[frame["direction"] == direction]
        if industry:
            frame = frame[frame["industry"] == industry]
        if min_pct is not None:
            frame = frame[frame["change_pct_total"] >= min_pct]
        frame = frame.sort_values(["notice_date", "amount"], ascending=[False, False])
        columns = ["change_key", "symbol", "name", "industry", "holder", "direction", "change_shares",
                   "change_pct_total", "hold_pct_after", "avg_price", "amount", "channel", "start_date", "end_date",
                   "notice_date"]
        return {"as_of": state["as_of"], "days": days, "total": len(frame), "rows": records(frame[columns])}

    def industries(self, days: int = 90) -> dict[str, Any]:
        """Per SW L1 industry over the last ``days``: companies with a live buyback, their planned amount, and
        the companies whose holders increased or decreased (with the amounts)."""
        state = self._load()
        since = state["as_of"] - timedelta(days=days)
        plans = state["plans"]
        plans = plans[(plans["latest_notice_date"] >= since) & plans["progress"].isin(LIVE_PROGRESS)]
        changes = state["changes"][state["changes"]["notice_date"] >= since]
        rows = {}
        for name, part in plans.groupby("industry"):
            rows.setdefault(name, {})["buyback_companies"] = int(part["symbol"].nunique())
            rows[name]["buyback_amount"] = float(part["amount_lower"].fillna(part["done_amount"]).sum())
            rows[name]["cancel_companies"] = int(part.loc[part["kind"] == "cancel", "symbol"].nunique())
        for (name, direction), part in changes.groupby(["industry", "direction"]):
            key = "increase" if direction == "增持" else "decrease"
            rows.setdefault(name, {})[f"{key}_companies"] = int(part["symbol"].nunique())
            rows[name][f"{key}_amount"] = float(part["amount"].sum())
        out = [{"industry": name, **{k: values.get(k, 0) for k in (
            "buyback_companies", "buyback_amount", "cancel_companies", "increase_companies", "increase_amount",
            "decrease_companies", "decrease_amount")}} for name, values in rows.items() if name]
        out.sort(key=lambda row: -row["buyback_companies"])
        return {"as_of": state["as_of"], "days": days, "rows": out}

    def tags(self, symbol: str, days: int = TAG_DAYS) -> dict[str, Any] | None:
        """公司在买 (a live buyback or a holder increase) and 减持 of one stock over the last ``days``."""
        state = self._load()
        since = state["as_of"] - timedelta(days=days)
        plans = state["plans"]
        plans = plans[(plans["symbol"] == symbol) & (plans["latest_notice_date"] >= since)
                      & plans["progress"].isin(LIVE_PROGRESS)]
        changes = state["changes"]
        changes = changes[(changes["symbol"] == symbol) & (changes["notice_date"] >= since)]
        increases, decreases = changes[changes["direction"] == "增持"], changes[changes["direction"] == "减持"]
        if plans.empty and changes.empty:
            return None
        notes = [f"{row.latest_notice_date} 回购{row.progress_name}（{KINDS.get(row.kind, '其他')}，"
                 f"计划下限约占市值 {row.plan_pct_lower:.2f}%）" if pd.notna(row.plan_pct_lower)
                 else f"{row.latest_notice_date} 回购{row.progress_name}" for row in plans.itertuples(index=False)]
        notes += [f"{row.notice_date} {row.holder} {row.direction} {row.change_pct_total:.2f}%"
                  if pd.notna(row.change_pct_total) else f"{row.notice_date} {row.holder} {row.direction}"
                  for row in changes.itertuples(index=False)]
        return {"buying": bool(len(plans) or len(increases)), "reducing": bool(len(decreases)), "days": days,
                "notes": notes[:8]}


_SHARED: dict[Path, CompanyActions] = {}
_SHARED_LOCK = threading.Lock()


def company_actions_for(database: Path) -> CompanyActions:
    with _SHARED_LOCK:
        if database not in _SHARED:
            _SHARED[database] = CompanyActions(database)
        return _SHARED[database]
