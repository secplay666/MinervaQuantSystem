"""Money map queries (analytics/money_map.py) from the sw_index_bars table of
``market.duckdb``, for the API and the position manager's reminders.

Computed once per catalog version (the file's mtime) and shared by the API
and the nightly job through ``money_map_for``; a whole history is 31 indices
x about 3,000 sessions and takes well under a second.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..analytics.money_map import (
    BASE_MIN,
    C_BANDS,
    CELLS,
    CROWDED,
    FIGHT,
    FIGHT_RETURN,
    HORIZONS,
    LONG,
    LONG_MIN,
    QUIET_RETURN,
    R_BANDS,
    SHORT,
    STARTING,
    STARTING_RETURN,
    ZONES,
    crossings,
    daily_measures,
    entries,
    pit_stats,
    stats_at,
    week_ends,
    weekly_table,
    zone_of,
)
from ..data_platform.sw_index import SW_L1

BENCHMARK = "sh000300"
# The panel starts once the 2021 classification's indices (28 of them before
# the three created in 2021) are all published, so every share has the same base.
MIN_INDUSTRIES = 28
DEFAULT_WEEKS = 156
CODE_OF = {name: code for code, name in SW_L1.items()}


class MoneyMapMissing(RuntimeError):
    pass


def _num(value: Any, digits: int = 4) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _stats(raw: dict[str, Any]) -> dict[str, Any]:
    return {h: {k: (int(v) if k == "n" else _num(v)) for k, v in s.items()} for h, s in raw.items()}


class MoneyMapQueries:
    def __init__(self, database: Path) -> None:
        self.database = database
        self._state: tuple[float, dict[str, Any] | MoneyMapMissing] | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        try:
            self._load()
        except MoneyMapMissing:
            return False
        return True

    # -- loading -------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.database.is_file():
            raise MoneyMapMissing("行情库不存在")
        key = self.database.stat().st_mtime
        with self._lock:
            if self._state is None or self._state[0] != key:
                try:
                    self._state = (key, self._compute())
                except MoneyMapMissing as exc:  # remembered too: the board asks once per item
                    self._state = (key, exc)
            if isinstance(self._state[1], MoneyMapMissing):
                raise self._state[1]
            return self._state[1]

    def _compute(self) -> dict[str, Any]:
        try:
            with duckdb.connect(str(self.database), read_only=True) as con:
                bars = con.execute("SELECT symbol, trade_date, close, turnover_cny FROM sw_index_bars").fetchdf()
                bench = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY trade_date",
                                    [BENCHMARK]).fetchdf()
        except duckdb.CatalogException as exc:  # a catalog built before the SW indices were collected
            raise MoneyMapMissing(str(exc)) from exc
        if bars.empty:
            raise MoneyMapMissing("没有申万行业指数数据")
        bars["trade_date"] = pd.to_datetime(bars["trade_date"])
        close = bars.pivot(index="trade_date", columns="symbol", values="close").sort_index()
        amount = bars.pivot(index="trade_date", columns="symbol", values="turnover_cny").sort_index()
        complete = amount.notna().sum(axis=1) >= MIN_INDUSTRIES
        if not complete.any():
            raise MoneyMapMissing("申万行业指数还不全")
        start = complete.idxmax()
        close, amount = close.loc[start:], amount.loc[start:]
        benchmark = pd.Series(bench["close"].to_numpy(), index=pd.to_datetime(bench["trade_date"]))
        daily = daily_measures(close, amount)
        table = weekly_table(close, amount, benchmark)
        if table.empty:
            raise MoneyMapMissing("申万行业指数的历史还不够计算拥挤度")
        weeks = [w.date() for w in week_ends(close.index)]  # indexed by week_no
        full = table[table["full_base"]]
        events = entries(table)
        events["key"] = "entry"
        n_weeks = len(weeks)
        return {
            "start": start.date(), "as_of": close.index[-1].date(), "daily": daily, "close": close,
            "benchmark": benchmark, "table": table, "weeks": weeks,
            "shown": sorted(int(n) for n in table["week_no"].unique()),  # weeks with a C to show
            "cells": pit_stats(full, "cell", n_weeks), "zones": pit_stats(full, "zone", n_weeks),
            "entries": events, "entry_stats": pit_stats(events[events["full_base"]], "key", n_weeks),
            "first": {code: close[code].first_valid_index().date() for code in close.columns},
            "crossings": {},
        }

    # -- queries -------------------------------------------------------------

    def rules(self) -> dict[str, Any]:
        return {
            "short": SHORT, "long": LONG, "long_min": LONG_MIN, "base_min": BASE_MIN, "crowded": CROWDED,
            "fight": [*FIGHT, FIGHT_RETURN], "starting": [*STARTING, *STARTING_RETURN], "quiet_return": QUIET_RETURN,
            "horizons": list(HORIZONS), "zones": ZONES, "cells": CELLS,
            "c_bands": [label for _, _, label in C_BANDS], "r_bands": [label for _, _, label in R_BANDS],
        }

    def overview(self, weeks: int | None = DEFAULT_WEEKS) -> dict[str, Any]:
        """The weekly frames for the replay (the last ``weeks``, or all), each
        with the statistics known that week for the cells and zones on it."""
        state = self._load()
        table = state["table"]
        shown = state["shown"][-weeks:] if weeks else state["shown"]
        frames = []
        for week_no in shown:
            rows = table[table["week_no"] == week_no]
            cells = {cell: _stats(stats_at(state["cells"], week_no, cell)) for cell in rows["cell"].dropna().unique()}
            zones = {zone: _stats(stats_at(state["zones"], week_no, zone)) for zone in rows["zone"].dropna().unique()}
            frames.append({
                "week": state["weeks"][week_no],
                "rows": [{"code": r.code, "c": _num(r.c, 3), "dc60": _num(r.dc60, 3), "r3": _num(r.r3),
                          "r12": _num(r.r12), "share": _num(r.share, 5), "zone": r.zone, "cell": r.cell,
                          "short": not bool(r.full_base)} for r in rows.itertuples(index=False)],
                "cells": cells, "zones": zones,
                "entry": _stats(stats_at(state["entry_stats"], week_no, "entry")),
            })
        events = state["entries"]
        return {
            "as_of": state["as_of"], "start": state["start"], "weeks_total": len(state["shown"]),
            "rules": self.rules(),
            "industries": [{"code": code, "name": SW_L1.get(code, code), "first": state["first"][code]}
                           for code in sorted(state["first"])],
            "frames": frames,
            "entries": [{"week": pd.Timestamp(e.week).date(), "code": e.code, "c": _num(e.c, 3), "r12": _num(e.r12),
                         **{f"{k}{h}": _num(getattr(e, f"{k}{h}")) for h in HORIZONS for k in ("x", "h")}}
                        for e in events.itertuples(index=False)],
        }

    def industry(self, code: str) -> dict[str, Any]:
        """One industry's daily C, turnover share and strength against the benchmark."""
        state = self._load()
        if code not in state["close"].columns:
            raise KeyError(code)
        close = state["close"][code]
        bench = state["benchmark"].reindex(close.index).ffill()
        valid = close.notna() & bench.notna()
        first = valid.idxmax()
        relative = (close / close.loc[first]) / (bench / bench.loc[first])
        daily = state["daily"]
        frame = pd.DataFrame({"c": daily["c"][code], "share": daily["share"][code], "close": close,
                              "relative": relative}).loc[first:]
        events = state["entries"][state["entries"]["code"] == code]
        return {"code": code, "name": SW_L1.get(code, code), "as_of": state["as_of"],
                "dates": [d.date() for d in frame.index],
                "c": [_num(v, 3) for v in frame["c"]], "share": [_num(v, 5) for v in frame["share"]],
                "close": [_num(v, 2) for v in frame["close"]], "relative": [_num(v, 4) for v in frame["relative"]],
                "entries": [pd.Timestamp(w).date() for w in events["week"]]}

    def crowding(self, code: str, high: float = CROWDED, low: float = FIGHT[1]) -> dict[str, Any] | None:
        """An industry's latest C and zone, with its crossing events for the
        position manager's reminders (C above ``high``; back below ``low``)."""
        state = self._load()
        if code not in state["close"].columns:
            return None
        key = (code, high, low)
        if key not in state["crossings"]:
            c = state["daily"]["c"][code]
            state["crossings"][key] = crossings([d.date() for d in c.index], c.to_numpy(dtype=float), high, low)
        c = state["daily"]["c"][code].dropna()
        if c.empty:
            return None
        r12 = state["daily"]["r12"][code].loc[c.index[-1]]
        zone = zone_of(np.array([c.iloc[-1]]), np.array([r12]))[0]
        return {"code": code, "name": SW_L1.get(code, code), "c": float(c.iloc[-1]), "as_of": c.index[-1].date(),
                "zone": zone, "zone_name": ZONES.get(zone) if zone else None, "high": high, "low": low,
                "events": state["crossings"][key]}


_SHARED: dict[Path, MoneyMapQueries] = {}
_SHARED_LOCK = threading.Lock()


def money_map_for(database: Path) -> MoneyMapQueries:
    """The process-wide queries object of one catalog, so the API and the
    position manager compute the map once."""
    with _SHARED_LOCK:
        if database not in _SHARED:
            _SHARED[database] = MoneyMapQueries(database)
        return _SHARED[database]


def industry_code(name: str | None) -> str | None:
    """The SW L1 index code of an industry name from ``industry_sw``."""
    return CODE_OF.get(name) if name else None
