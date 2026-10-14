"""期权: SSE ETF options and CFFEX index options (data_platform/options.py) for the options page.

* the underlyings, with their history span and contract counts;
* a T-shaped chain: the calls and puts of one expiry month on one session, by strike;
* one contract's daily bars with its underlying's close, implied volatility and delta (SSE), and its intrinsic
  and time value.  SSE volume and open interest come from the minute bars (option_minute_totals), so they start
  with the first session collected;
* one contract's minute bars of a session (SSE, from the first session collected; read from the partition,
  not the catalog).

Underlying prices: the ETFs' unadjusted closes (etf_bars) and the indices (index_bars) for CFFEX.  An adjusted
SSE contract (code with A, B, ...) is valued at the strike in its name, the one after the adjustment.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..data_platform.options import CFFEX_PRODUCTS, SSE_UNDERLYINGS
from .market import records

UNDERLYINGS: dict[str, dict[str, str]] = {
    **{code: {"name": name, "exchange": "SSE", "table": "etf_bars", "symbol": code}
       for code, name in SSE_UNDERLYINGS.items()},
    **{code: {"name": name, "exchange": "CFFEX", "table": "index_bars", "symbol": index}
       for code, (name, index) in CFFEX_PRODUCTS.items()},
}
UNIT_TEXT = {"SSE": "一张 = 10,000 份（调整过的合约另计）", "CFFEX": "一张 = 每点 100 元"}
SIDE_COLUMNS = ["contract", "name", "close", "prev_close", "change", "change_pct", "volume", "open_interest",
                "settle", "iv", "delta", "intrinsic", "time_value"]


class OptionsMissing(RuntimeError):
    pass


class OptionQueries:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.minutes_root = database.parent / "canonical" / "option_minute_bars"
        self._cache: tuple[float, pd.DataFrame, pd.DataFrame, bool] | None = None
        self._lock = threading.Lock()

    # -- plumbing -------------------------------------------------------------------------------------------

    def _query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        if not self.database.is_file():
            raise OptionsMissing("行情库不存在")
        try:
            with duckdb.connect(str(self.database), read_only=True) as con:
                return con.execute(sql, params or []).fetchdf()
        except duckdb.CatalogException as exc:  # a catalog built before the options step ran
            raise OptionsMissing("还没有期权数据") from exc

    def _load(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(contract terms, per-underlying span), once per catalog version; also whether minute totals exist."""
        if not self.database.is_file():
            raise OptionsMissing("行情库不存在")
        key = self.database.stat().st_mtime
        with self._lock:
            if self._cache is None or self._cache[0] != key:
                terms = self._query("SELECT contract, exchange, underlying, code, name, kind, expiry_month, "
                                    "expiry_date, strike, adjusted, multiplier FROM option_contracts")
                terms["expiry_date"] = pd.to_datetime(terms["expiry_date"]).dt.date
                span = self._query("SELECT underlying, min(trade_date) AS first_date, max(trade_date) AS last_date, "
                                   "count(DISTINCT contract) AS contracts FROM option_daily_bars GROUP BY 1")
                for column in ("first_date", "last_date"):
                    span[column] = pd.to_datetime(span[column]).dt.date
                tables = set(self._query("SELECT table_name FROM information_schema.tables")["table_name"])
                self._cache = (key, terms, span.set_index("underlying"), "option_minute_totals" in tables)
            return self._cache[1], self._cache[2]

    def _bars_sql(self, columns: str) -> str:
        """Daily bars as ``b`` with volume and open interest from the minute totals where the bar has none."""
        joined = self._cache is not None and self._cache[3]
        volume = "coalesce(b.volume, t.volume)" if joined else "b.volume"
        interest = "coalesce(b.open_interest, t.open_interest)" if joined else "b.open_interest"
        source = ("option_daily_bars b LEFT JOIN option_minute_totals t ON t.contract = b.contract "
                  "AND t.trade_date = b.trade_date") if joined else "option_daily_bars b"
        return (f"SELECT {columns}, {volume} AS volume, {interest} AS open_interest FROM {source} "
                "WHERE b.underlying = ? AND ")

    def _underlying_closes(self, underlying: str, start: date, end: date) -> pd.Series:
        spec = UNDERLYINGS[underlying]
        frame = self._query(f"SELECT trade_date, close FROM {spec['table']} WHERE symbol = ? AND trade_date "
                            "BETWEEN ? AND ? ORDER BY trade_date", [spec["symbol"], start, end])
        return pd.Series(frame["close"].to_numpy(dtype=float), index=pd.to_datetime(frame["trade_date"]).dt.date)

    def _own(self, underlying: str) -> tuple[pd.DataFrame, date]:
        """The underlying's contracts and the latest session of all options (what "expired" is measured against)."""
        terms, span = self._load()
        if underlying not in UNDERLYINGS:
            raise LookupError(f"没有这个标的：{underlying}")
        own = terms[terms["underlying"] == underlying]
        if own.empty or underlying not in span.index:
            raise LookupError(f"{UNDERLYINGS[underlying]['name']} 还没有期权数据")
        return own, span["last_date"].max()

    # -- queries --------------------------------------------------------------------------------------------

    def underlyings(self) -> dict[str, Any]:
        terms, span = self._load()
        latest = span["last_date"].max() if not span.empty else None
        rows = []
        for code, spec in UNDERLYINGS.items():
            if code not in span.index:
                continue
            own = terms[terms["underlying"] == code]
            rows.append({"underlying": code, "name": spec["name"], "exchange": spec["exchange"],
                         "first_date": span.loc[code, "first_date"], "last_date": span.loc[code, "last_date"],
                         "contracts": int(len(own)), "live": int((own["expiry_date"] >= latest).sum()),
                         "minutes": spec["exchange"] == "SSE", "unit": UNIT_TEXT[spec["exchange"]]})
        return {"underlyings": rows}

    def chain(self, underlying: str, month: str | None = None, day: date | None = None) -> dict[str, Any]:
        """The calls and puts of ``month`` on ``day`` (default: the nearest month not expired, its latest session)."""
        own, last = self._own(underlying)
        months = (own.groupby("expiry_month").agg(expiry_date=("expiry_date", "min"), contracts=("contract", "size"))
                  .reset_index().sort_values("expiry_month"))
        months["expired"] = months["expiry_date"] < last
        if month is None:
            live = months[~months["expired"]]
            month = (live if not live.empty else months).iloc[0 if not live.empty else -1]["expiry_month"]
        if month not in set(months["expiry_month"]):
            raise LookupError(f"{underlying} 没有 {month} 到期的合约")
        chosen = own[own["expiry_month"] == month]
        ids = list(chosen["contract"])
        marks = ",".join("?" * len(ids))
        bars = self._query(self._bars_sql("b.contract, b.trade_date, b.close, b.settle, b.exchange_delta")
                           + f"b.contract IN ({marks})", [underlying, *ids])
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
        sessions = sorted(bars["trade_date"].unique())
        if not sessions:
            raise LookupError(f"{underlying} {month} 没有行情")
        pick = day if day is not None else sessions[-1]
        i = int(np.searchsorted(sessions, pick, side="right")) - 1
        day = sessions[max(i, 0)]
        prev = sessions[sessions.index(day) - 1] if sessions.index(day) > 0 else None
        today = bars[bars["trade_date"] == day].set_index("contract")
        before = bars[bars["trade_date"] == prev].set_index("contract")["close"] if prev else pd.Series(dtype=float)
        risk = pd.DataFrame(columns=["contract", "iv", "delta"])
        if UNDERLYINGS[underlying]["exchange"] == "SSE":
            risk = self._query(f"SELECT contract, iv, delta FROM option_risk WHERE underlying = ? AND trade_date = ? "
                               f"AND contract IN ({marks})", [underlying, day, *ids])
        risk = risk.set_index("contract")
        closes = self._underlying_closes(underlying, prev or day, day)
        spot = closes.get(day)
        spot_prev = closes.get(prev) if prev else None

        def side(term: pd.Series) -> dict[str, Any] | None:
            cid = term["contract"]
            if cid not in today.index:
                return None
            bar = today.loc[cid]
            close, prev_close = bar["close"], before.get(cid, np.nan)
            intrinsic = _intrinsic(term["kind"], spot, term["strike"])
            delta = risk["delta"].get(cid, np.nan) if cid in risk.index else bar["exchange_delta"]
            values = {"contract": cid, "name": term["name"], "close": close, "prev_close": prev_close,
                      "change": close - prev_close, "change_pct": (close / prev_close - 1) * 100 if prev_close else None,
                      "volume": bar["volume"], "open_interest": bar["open_interest"], "settle": bar["settle"],
                      "iv": risk["iv"].get(cid, np.nan) if cid in risk.index else np.nan, "delta": delta,
                      "intrinsic": intrinsic, "time_value": close - intrinsic if intrinsic is not None else None}
            return {k: _clean(values[k]) for k in SIDE_COLUMNS}

        rows = []
        standard = chosen[~chosen["adjusted"].astype(bool) & chosen["contract"].isin(today.index)]
        atm = None
        if spot is not None and not standard.empty:
            atm = float(standard.iloc[(standard["strike"] - spot).abs().argsort().iloc[0]]["strike"])
        for (strike, adjusted), group in chosen.groupby(["strike", "adjusted"], sort=True):
            by_kind = {row["kind"]: row for _, row in group.iterrows()}
            call = side(by_kind["call"]) if "call" in by_kind else None
            put = side(by_kind["put"]) if "put" in by_kind else None
            if call is None and put is None:
                continue  # not listed on that session
            rows.append({"strike": float(strike), "adjusted": bool(adjusted), "atm": atm == float(strike) and not adjusted,
                         "call": call, "put": put})
        expiry = chosen["expiry_date"].min()
        return {
            "underlying": {"code": underlying, **{k: UNDERLYINGS[underlying][k] for k in ("name", "exchange")},
                           "close": _clean(spot),
                           "change_pct": _clean((spot / spot_prev - 1) * 100) if spot and spot_prev else None},
            "months": records(months),
            "month": month, "expiry_date": expiry, "expired": bool(expiry < last), "day": day,
            "days_left": (expiry - day).days, "sessions": sessions, "rows": rows,
            "unit": UNIT_TEXT[UNDERLYINGS[underlying]["exchange"]],
        }

    def contract(self, contract: str) -> dict[str, Any]:
        """A contract's terms and daily history with the underlying, implied volatility and intrinsic value."""
        terms, _ = self._load()
        found = terms[terms["contract"] == contract]
        if found.empty:
            raise LookupError(f"没有这个合约：{contract}")
        term = found.iloc[0]
        underlying = term["underlying"]
        bars = self._query(self._bars_sql("b.trade_date, b.open, b.high, b.low, b.close, b.amount, b.settle")
                           + "b.contract = ? ORDER BY b.trade_date", [underlying, contract])
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
        if UNDERLYINGS[underlying]["exchange"] == "SSE":
            risk = self._query("SELECT trade_date, iv, delta, gamma, theta, vega FROM option_risk "
                               "WHERE underlying = ? AND contract = ?", [underlying, contract])
            risk["trade_date"] = pd.to_datetime(risk["trade_date"]).dt.date
            bars = bars.merge(risk, on="trade_date", how="left")
        minute_days: list[date] = []
        if not bars.empty:
            first, last = bars["trade_date"].iloc[0], bars["trade_date"].iloc[-1]
            spot = self._underlying_closes(underlying, first, last)
            bars["underlying_close"] = bars["trade_date"].map(spot)
            bars["intrinsic"] = [_intrinsic(term["kind"], s, term["strike"]) for s in bars["underlying_close"]]
            bars["time_value"] = bars["close"] - bars["intrinsic"].astype(float)
            if UNDERLYINGS[underlying]["exchange"] == "SSE":
                minute_days = [d for d in self._minute_sessions() if first <= d <= last]
        info = {k: _clean(term[k]) for k in ("contract", "exchange", "underlying", "code", "name", "kind",
                                              "expiry_month", "expiry_date", "strike", "multiplier")}
        info["adjusted"] = bool(term["adjusted"])
        info["underlying_name"] = UNDERLYINGS[underlying]["name"]
        return {"terms": info, "bars": records(bars), "minute_days": minute_days,
                "unit": UNIT_TEXT[UNDERLYINGS[underlying]["exchange"]]}

    def minutes(self, contract: str, day: date) -> dict[str, Any]:
        """1-minute bars of a session; ``prev_close`` is the daily close before it (the change baseline)."""
        terms, _ = self._load()
        found = terms[terms["contract"] == contract]
        if found.empty:
            raise LookupError(f"没有这个合约：{contract}")
        path = self.minutes_root / f"trade_date={day.isoformat()}" / "data.parquet"
        rows = pd.DataFrame(columns=["time", "price", "volume", "open_interest", "avg_price"])
        if path.is_file():
            frame = pd.read_parquet(path, columns=["contract", "time", "price", "volume", "open_interest", "avg_price"],
                                    filters=[("contract", "==", contract)])
            rows = frame.drop(columns="contract").sort_values("time")
        prev = self._query("SELECT close FROM option_daily_bars WHERE underlying = ? AND contract = ? "
                           "AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                           [found.iloc[0]["underlying"], contract, day])
        return {"contract": contract, "day": day, "prev_close": _clean(prev["close"].iloc[0]) if len(prev) else None,
                "rows": records(rows)}

    def _minute_sessions(self) -> list[date]:
        if not self.minutes_root.is_dir():
            return []
        days = []
        for child in self.minutes_root.iterdir():
            if child.name.startswith("trade_date=") and (child / "data.parquet").is_file():
                days.append(date.fromisoformat(child.name.split("=", 1)[1]))
        return sorted(days)


def _intrinsic(kind: str, spot: float | None, strike: float) -> float | None:
    if spot is None or spot != spot:
        return None
    return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else round(float(value), 6)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def options_for(database: Path) -> OptionQueries:
    return OptionQueries(database)
