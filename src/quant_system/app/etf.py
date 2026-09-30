"""ETF flow dashboard: broad-index groups, daily net subscriptions, abnormal
days (analytics/etf_flows.py) from the etf_* tables of ``market.duckdb``.

Everything is computed once per catalog version (the file's mtime) and kept
in memory; a whole history is about 130 funds x 2,900 days.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..analytics.etf_flows import (
    ABNORMAL_MIN_SHARE,
    ABNORMAL_Z,
    BASELINE_SESSIONS,
    STRONG_MIN_SHARE,
    STRONG_Z,
    fund_flows,
    group_flows,
)
from ..data_platform.etf import group_members, load_etf_groups
from ..data_platform.etf_holders import classify_holder
from .market import records

ALL_GROUP = {"id": "all", "name": "全部宽基", "chart_symbol": "sh000300", "index_codes": []}


class EtfDataMissing(RuntimeError):
    pass


class EtfQueries:
    def __init__(self, database: Path, groups_path: Path) -> None:
        self.database = database
        self.groups_path = groups_path
        self._state: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            self._load()
        except EtfDataMissing:
            return False
        return True

    # -- loading -------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.database.is_file():
            raise EtfDataMissing("行情库不存在")
        key = self.database.stat().st_mtime
        with self._lock:
            if self._state is None or self._state[0] != key:
                self._state = (key, self._compute())
            return self._state[1]

    def _compute(self) -> dict[str, Any]:
        config = load_etf_groups(self.groups_path)
        try:
            with duckdb.connect(str(self.database), read_only=True) as con:
                master = con.execute("SELECT exchange, symbol, name, index_code, index_name, list_date, "
                                     "listed_run_id FROM etf_master").fetchdf()
                members = group_members(master, config)
                symbols = members["symbol"].astype(str).tolist()
                shares = con.execute("SELECT trade_date, exchange, symbol, shares FROM etf_shares "
                                     "WHERE list_contains(?, symbol)", [symbols]).fetchdf()
                closes = con.execute("SELECT trade_date, symbol, close FROM etf_bars "
                                     "WHERE list_contains(?, symbol)", [symbols]).fetchdf()
                sessions = con.execute("SELECT trade_date FROM trading_calendar ORDER BY 1").fetchdf()
                try:
                    holders = con.execute("SELECT symbol, report_date, report_type, notice_date, rank, holder, "
                                          "shares, pct, feeder FROM etf_top_holders "
                                          "WHERE list_contains(?, symbol)", [symbols]).fetchdf()
                except duckdb.CatalogException:  # periodic reports not collected yet
                    holders = pd.DataFrame(columns=["symbol", "report_date", "report_type", "notice_date", "rank",
                                                    "holder", "shares", "pct", "feeder"])
        except duckdb.CatalogException as exc:  # a catalog built before the ETF step existed
            raise EtfDataMissing(str(exc)) from exc
        for frame in (shares, closes):
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        calendar = sorted(pd.to_datetime(sessions["trade_date"]).dt.date)
        if shares.empty:
            raise EtfDataMissing("没有 ETF 份额数据")
        # SZSE publishes a day later than SSE: days after the last one both have are incomplete.
        latest = shares.groupby("exchange")["trade_date"].max()
        complete_until = latest.min()
        funds = fund_flows(shares, closes, calendar).merge(members[["symbol", "group_id"]], on="symbol")
        groups = [*config["groups"], ALL_GROUP]
        daily = {}
        for group in groups:
            part = funds if group["id"] == "all" else funds[funds["group_id"] == group["id"]]
            series = group_flows(part)
            partial = series["trade_date"] > complete_until
            series["partial"] = partial
            series.loc[partial, ["z", "abnormal"]] = None
            series.loc[partial, "strong"] = False
            daily[group["id"]] = series
        names = members.drop_duplicates("symbol").set_index("symbol")
        return {"groups": groups, "daily": daily, "funds": funds, "names": names, "config": config,
                "holders": _classified(holders, config), "complete_until": complete_until, "latest": latest.max()}

    def _group(self, state: dict[str, Any], group_id: str) -> dict[str, Any]:
        for group in state["groups"]:
            if group["id"] == group_id:
                return group
        raise KeyError(group_id)

    # -- queries -------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        state = self._load()
        rows = []
        for group in state["groups"]:
            series = state["daily"][group["id"]]
            complete = series[~series["partial"]]
            last = complete.iloc[-1] if len(complete) else None
            recent = complete.tail(BASELINE_SESSIONS)
            abnormal = complete[complete["abnormal"].notna()]
            flows = complete["flow"].to_numpy()
            rows.append({
                "id": group["id"], "name": group["name"], "chart_symbol": group["chart_symbol"],
                "funds": int(last["funds"]) if last is not None else 0,
                "aum": _num(last["aum"]) if last is not None else None,
                **{f"flow_{n}d": _num(np.nansum(flows[-n:])) if len(flows) else None for n in (1, 5, 20, 60, 250)},
                "abnormal_in_250d": int((recent["abnormal"] == "in").sum()),
                "abnormal_out_250d": int((recent["abnormal"] == "out").sum()),
                "strong_250d": int(recent["strong"].sum()),
                "last_abnormal": records(abnormal.tail(1)[["trade_date", "abnormal", "flow", "flow_pct", "z",
                                                           "strong"]])[0]
                if len(abnormal) else None,
            })
        return {"as_of": state["complete_until"], "latest": state["latest"], "groups": rows,
                "rules": {"z": ABNORMAL_Z, "min_share": ABNORMAL_MIN_SHARE, "baseline": BASELINE_SESSIONS,
                          "strong_z": STRONG_Z, "strong_min_share": STRONG_MIN_SHARE}}

    def series(self, group_id: str, start: date | None = None, end: date | None = None) -> dict[str, Any]:
        state = self._load()
        group = self._group(state, group_id)
        frame = state["daily"][group_id]
        if start is not None:
            frame = frame[frame["trade_date"] >= start]
        if end is not None:
            frame = frame[frame["trade_date"] <= end]
        frame = frame.assign(cumulative=frame["flow"].fillna(0).cumsum())
        return {"group": {k: group[k] for k in ("id", "name", "chart_symbol")},
                "as_of": state["complete_until"],
                "rows": records(frame[["trade_date", "aum", "flow", "flow_pct", "cumulative", "z", "abnormal", "strong",
                                       "funds", "events", "partial"]])}

    def marks(self, chart_symbol: str) -> list[dict[str, Any]]:
        """Abnormal days of the groups charted on this index, for the K-line workstation."""
        state = self._load()
        rows = []
        for group in state["groups"]:
            if group["id"] == "all" or group["chart_symbol"] != chart_symbol:
                continue
            frame = state["daily"][group["id"]]
            frame = frame[frame["abnormal"].notna()]
            rows += [{**row, "group": group["name"]}
                     for row in records(frame[["trade_date", "abnormal", "flow", "flow_pct", "z", "strong"]])]
        return sorted(rows, key=lambda row: row["trade_date"])

    def holders(self, group_id: str) -> dict[str, Any]:
        """National-team holdings of a group's funds per report period (the
        top-holder tables of annual and interim reports), valued at the fund's
        close on the period end; and each fund's latest table."""
        state = self._load()
        self._group(state, group_id)
        classes = [{"id": c["id"], "name": c["name"]} for c in state["config"].get("holder_classes", [])]
        funds = state["funds"] if group_id == "all" else state["funds"][state["funds"]["group_id"] == group_id]
        table = state["holders"]
        table = table[table["symbol"].isin(set(funds["symbol"])) & ~table["feeder"]]
        if table.empty:
            return {"classes": classes, "periods": [], "funds": [], "latest_period": None}
        closes = funds[["trade_date", "symbol", "close"]].dropna()
        closes = closes.assign(at=pd.to_datetime(closes["trade_date"])).sort_values("at")[["at", "symbol", "close"]]
        left = table.assign(at=pd.to_datetime(table["report_date"])).sort_values("at")
        valued = pd.merge_asof(left, closes, on="at", by="symbol", direction="backward").drop(columns="at")
        valued["value"] = valued["shares"] * valued["close"]
        daily = state["daily"][group_id].sort_values("trade_date")
        periods = []
        for report_date, rows in valued.groupby("report_date"):
            by_class = {c["id"]: _num(rows.loc[rows["holder_class"] == c["id"], "value"].sum()) for c in classes}
            aum_rows = daily[daily["trade_date"] <= report_date]
            aum = _num(aum_rows["aum"].iloc[-1]) if len(aum_rows) else None
            national = float(rows.loc[rows["holder_class"].notna(), "value"].sum())
            periods.append({"report_date": report_date, "funds": int(rows["symbol"].nunique()), "by_class": by_class,
                            "national_value": national, "aum": aum,
                            "national_share": national / aum if aum else None})
        latest = valued.sort_values("report_date").groupby("symbol").tail(100)
        latest = latest[latest["report_date"] == latest.groupby("symbol")["report_date"].transform("max")]
        rows = []
        for symbol, part in latest.groupby("symbol"):
            part = part.sort_values("rank")
            national = part[part["holder_class"].notna()]
            info = state["names"].loc[symbol]
            rows.append({"symbol": symbol, "name": info["name"], "exchange": info["exchange"],
                         "report_date": part["report_date"].iloc[0], "notice_date": part["notice_date"].iloc[0],
                         "national_pct": float(national["pct"].sum()), "national_value": float(national["value"].sum()),
                         "by_class": {c["id"]: float(national.loc[national["holder_class"] == c["id"], "pct"].sum())
                                      for c in classes},
                         "holders": records(part[["rank", "holder", "shares", "pct", "holder_class"]])})
        rows.sort(key=lambda r: -r["national_value"])
        return {"classes": classes, "periods": periods, "funds": rows,
                "latest_period": max(p["report_date"] for p in periods)}

    def funds(self, group_id: str, day: date | None = None) -> dict[str, Any]:
        """Each fund of a group on ``day`` (default: the last complete day):
        size, that day's flow and flows over the 5/20/60 sessions ending then."""
        state = self._load()
        self._group(state, group_id)
        funds = state["funds"] if group_id == "all" else state["funds"][state["funds"]["group_id"] == group_id]
        day = day or state["complete_until"]
        funds = funds[funds["trade_date"] <= day]
        dates = sorted(funds["trade_date"].unique())
        if not dates:
            return {"date": day, "rows": []}
        cut = {n: dates[max(0, len(dates) - n)] for n in (5, 20, 60)}
        on_day = funds[funds["trade_date"] == dates[-1]].set_index("symbol")
        rows = []
        for symbol, fund in on_day.iterrows():
            history = funds[funds["symbol"] == symbol]
            info = state["names"].loc[symbol]
            events = history[history["event"].isin(["split", "jump", "gap"])].tail(3)
            rows.append({
                "symbol": symbol, "name": info["name"], "exchange": info["exchange"], "group_id": fund["group_id"],
                "shares": _num(fund["shares"]), "close": _num(fund["close"]), "aum": _num(fund["aum"]),
                "flow": _num(fund["flow"]), "event": fund["event"],
                **{f"flow_{n}d": _num(history.loc[history["trade_date"] >= cut[n], "flow"].sum()) for n in cut},
                "recent_events": records(events[["trade_date", "event", "share_change"]]),
            })
        rows.sort(key=lambda row: -(row["aum"] or 0))
        return {"date": dates[-1], "rows": rows}


def _classified(holders: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Holder classes (configs/etf/broad_groups.json holder_classes) assigned at query time."""
    classes = {c["id"]: c["patterns"] for c in config.get("holder_classes", [])}
    frame = holders.copy()
    for column in ("report_date", "notice_date"):
        frame[column] = pd.to_datetime(frame[column]).dt.date
    frame["feeder"] = frame["feeder"].fillna(False).astype(bool)
    frame["holder_class"] = [classify_holder(str(name), classes) for name in frame["holder"]]
    return frame


def _num(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
