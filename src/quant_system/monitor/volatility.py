"""A-share volatility indices (恐慌指数): the 30-day volatility the option market prices in, by CBOE's VIX method.

Per index (configs/monitor/volatility.json): the SSE options of an ETF (300ETF, 50ETF) or the CFFEX options of an
index (沪深300 IO, 中证1000 MO).  At each poll the monitor reads the two nearest expiries at least ``min_days`` away
(the near one is rolled when closer, as the exchange's iVX did), takes the mid quote of every option with both a bid
and an ask, and computes CBOE's model-free variance of each term:

    σ² = 2/T · Σ ΔK/K² · e^{rT} · Q(K)  −  1/T · (F/K0 − 1)²

F the forward from put-call parity at the strike where call and put are closest, K0 the listed strike at or below F,
Q the out-of-the-money option at each strike (calls above K0, puts below, their average at K0), strikes taken outward
from K0 until two in a row have no quote.  The two terms are interpolated to 30 days; index = 100 · √σ²₃₀.  Time
runs on the calendar to 15:00 of the expiry day (as CBOE counts minutes to settlement); the rate is a flat config
value (its effect is small).

Daily history (``daily_history``): the same on each session's closes (SSE, Sina's daily bars) or settlement prices
(CFFEX), written to data/monitor/volatility/daily.parquet by ``quant-app monitor volatility-history`` after the
morning catch-up.  It gives today's percentile; the alert (one event per index and day) fires when an index stands
``alert_rise`` above the previous session's close and at or above ``alert_percentile`` of the last
``history_years``.  Information, not a signal.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)
EXPIRY_TIME = time(15, 0)
YEAR_SECONDS = 365 * 86400
THIRTY_DAYS = 30 / 365
MINUTE_COLUMNS = ["time", "id", "value", "near_vol", "next_vol", "near_expiry", "next_expiry", "forward", "strikes"]
HISTORY_FILE = Path("data") / "monitor" / "volatility" / "daily.parquet"  # quant-app monitor volatility-history
# SSE options' closing call auction: from 14:57 the quotes are indicative and thin (11-06: 300ETF had no usable strike
# at 14:57 and half its strikes at 14:58, and its index fell 22.0 -> 20.4 while the CFFEX ones did not move).
SSE_CLOSING_AUCTION = (time(14, 57), time(15, 0))


@dataclass(frozen=True)
class VolIndex:
    id: str
    name: str
    exchange: str    # SSE | CFFEX
    underlying: str  # an ETF code (510300) or a CFFEX product (IO, MO, HO)


@dataclass(frozen=True)
class VolConfig:
    indices: tuple[VolIndex, ...]
    risk_free: float = 0.015
    min_days: int = 7
    history_years: int = 3
    alert_rise: float = 0.2
    alert_percentile: float = 0.8
    min_strike_share: float = 0.6   # a minute with fewer strikes than this share of the day's median is not kept

    @classmethod
    def load(cls, path: Path) -> VolConfig:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        indices = tuple(VolIndex(**item) for item in payload.pop("indices"))
        return cls(indices=indices, **payload)


# -- the VIX method ------------------------------------------------------------------------------------------------

def years_to(expiry: date, now: datetime) -> float:
    return (datetime.combine(expiry, EXPIRY_TIME) - now).total_seconds() / YEAR_SECONDS


def pick_terms(expiries: list[date], now: datetime, min_days: int) -> list[date]:
    """The two nearest expiries at least ``min_days`` calendar days away."""
    return [e for e in sorted(set(expiries)) if years_to(e, now) * 365 >= min_days][:2]


def quote_table(chain: pd.DataFrame) -> pd.DataFrame:
    """Every listed strike (ascending) with the mid quote of its call and put; NaN without both a bid and an ask."""
    listed = sorted(chain["strike"].astype(float).unique())
    quoted = chain[(chain["bid"] > 0) & (chain["ask"] > 0) & (chain["ask"] >= chain["bid"])]
    mid = quoted.assign(mid=(quoted["bid"] + quoted["ask"]) / 2, strike=quoted["strike"].astype(float))
    table = mid.pivot_table(index="strike", columns="kind", values="mid", aggfunc="mean")
    return table.reindex(index=listed, columns=["call", "put"])


def term_variance(chain: pd.DataFrame, t: float, r: float) -> dict[str, float] | None:
    """CBOE's variance of one expiry from its chain (columns strike, kind call|put, bid, ask); None when too thin."""
    if t <= 0 or chain.empty:
        return None
    table = quote_table(chain)
    both = table.dropna()
    if both.empty:
        return None
    growth = math.exp(r * t)
    k_star = float((both["call"] - both["put"]).abs().idxmin())
    forward = k_star + growth * float(both.at[k_star, "call"] - both.at[k_star, "put"])
    strikes = list(table.index)
    below = [k for k in strikes if k <= forward]
    if not below:
        return None
    k0 = below[-1]
    at_k0 = table.loc[k0].dropna()
    if at_k0.empty:
        return None
    selected = {k0: float(at_k0.mean())}
    i0 = strikes.index(k0)
    for side, kind in ((reversed(strikes[:i0]), "put"), (strikes[i0 + 1:], "call")):
        misses = 0
        for k in side:
            q = table.at[k, kind]
            if np.isnan(q):
                misses += 1
                if misses == 2:
                    break
                continue
            misses = 0
            selected[k] = float(q)
    ks = sorted(selected)
    if len(ks) < 3:
        return None
    total = 0.0
    for j, k in enumerate(ks):
        dk = ks[1] - ks[0] if j == 0 else ks[-1] - ks[-2] if j == len(ks) - 1 else (ks[j + 1] - ks[j - 1]) / 2
        total += dk / k ** 2 * selected[k]
    variance = 2 / t * growth * total - (forward / k0 - 1) ** 2 / t
    return {"variance": variance, "t": t, "forward": forward, "strikes": float(len(ks))}


def thirty_day(near: dict | None, nxt: dict | None) -> float:
    """CBOE's interpolation of two terms to 30 days (with one term only, that term's volatility)."""
    if near is None:
        return float("nan")
    if nxt is None:
        return 100 * math.sqrt(near["variance"]) if near["variance"] > 0 else float("nan")
    t1, v1, t2, v2 = near["t"], near["variance"], nxt["t"], nxt["variance"]
    w = (t2 - THIRTY_DAYS) / (t2 - t1)
    var30 = (t1 * v1 * w + t2 * v2 * (1 - w)) / THIRTY_DAYS
    return 100 * math.sqrt(var30) if var30 > 0 else float("nan")


def index_value(chains: dict[date, pd.DataFrame], now: datetime, config: VolConfig) -> dict[str, Any]:
    """The index from the chains by expiry (only the two terms of ``pick_terms`` are used)."""
    terms = pick_terms(list(chains), now, config.min_days)
    results = [term_variance(chains[e], years_to(e, now), config.risk_free) for e in terms]
    near = results[0] if results else None
    nxt = results[1] if len(results) > 1 else None

    def vol(term: dict | None) -> float:
        return 100 * math.sqrt(term["variance"]) if term and term["variance"] > 0 else float("nan")

    return {"value": thirty_day(near, nxt), "near_vol": vol(near), "next_vol": vol(nxt),
            "near_expiry": terms[0] if terms else None, "next_expiry": terms[1] if len(terms) > 1 else None,
            "forward": near["forward"] if near else float("nan"),
            "strikes": sum(int(t["strikes"]) for t in (near, nxt) if t)}


# -- live chains (Sina) --------------------------------------------------------------------------------------------

SINA_LIST = "https://hq.sinajs.cn/list={}"
ADJUSTED_NAME = re.compile(r"[A-Z]$")   # an adjusted SSE contract's name ends in a letter (50ETF购1月2755A)
CFFEX_SPOT = {"IO": "option_cffex_hs300_spot_sina", "MO": "option_cffex_zz1000_spot_sina",
              "HO": "option_cffex_sz50_spot_sina"}


def parse_sse_quotes(text: str) -> pd.DataFrame:
    """Sina CON_OP_ quotes → contract, strike, bid, ask, last, name, time (adjusted contracts dropped)."""
    rows = []
    for match in re.finditer(r'hq_str_CON_OP_(\d+)="([^"]*)"', text):
        f = match.group(2).split(",")
        if len(f) < 38 or ADJUSTED_NAME.search(f[37].strip()):
            continue

        def num(i: int) -> float:
            try:
                return float(f[i])
            except (ValueError, IndexError):
                return float("nan")

        rows.append({"contract": match.group(1), "strike": num(7), "bid": num(1), "ask": num(3), "last": num(2),
                     "name": f[37].strip(), "time": f[32].strip()})
    return pd.DataFrame(rows, columns=["contract", "strike", "bid", "ask", "last", "name", "time"])


def parse_code_list(text: str) -> list[str]:
    return re.findall(r"CON_OP_(\d+)", text)


def cffex_long(spot: pd.DataFrame) -> pd.DataFrame:
    """AKShare's Sina T-quote of one CFFEX month → strike, kind, bid, ask."""
    parts = []
    for kind, side in (("call", "看涨合约"), ("put", "看跌合约")):
        parts.append(pd.DataFrame({"strike": pd.to_numeric(spot["行权价"], errors="coerce"), "kind": kind,
                                   "bid": pd.to_numeric(spot[f"{side}-买价"], errors="coerce"),
                                   "ask": pd.to_numeric(spot[f"{side}-卖价"], errors="coerce")}))
    return pd.concat(parts, ignore_index=True).dropna(subset=["strike"])


class SinaChains:
    """Live option chains: SSE from Sina hq (the month's call and put lists, then CON_OP_ quotes; the lists are
    read once a day), CFFEX from AKShare's Sina T-quote of the month."""

    def __init__(self, http_get: Callable[[str], str], cffex_spot: Callable[[str, str], pd.DataFrame] | None = None):
        self.http_get = http_get
        self.cffex_spot = cffex_spot or _akshare_cffex_spot
        self._codes: dict[tuple[str, str], tuple[list[str], list[str]]] = {}

    def chain(self, index: VolIndex, month: str) -> pd.DataFrame:
        """``month`` YYYY-MM → strike, kind, bid, ask."""
        yymm = month[2:4] + month[5:7]
        if index.exchange == "CFFEX":
            return cffex_long(self.cffex_spot(index.underlying, f"{index.underlying.lower()}{yymm}"))
        key = (index.underlying, yymm)
        if key not in self._codes:
            calls = parse_code_list(self.http_get(SINA_LIST.format(f"OP_UP_{index.underlying}{yymm}")))
            puts = parse_code_list(self.http_get(SINA_LIST.format(f"OP_DOWN_{index.underlying}{yymm}")))
            self._codes[key] = (calls, puts)
        calls, puts = self._codes[key]
        quotes = parse_sse_quotes(self.http_get(SINA_LIST.format(",".join(f"CON_OP_{c}" for c in calls + puts))))
        kinds = {c: "call" for c in calls} | {c: "put" for c in puts}
        return quotes.assign(kind=quotes["contract"].map(kinds))[["strike", "kind", "bid", "ask"]]


def _akshare_cffex_spot(product: str, symbol: str) -> pd.DataFrame:
    import akshare as ak

    return getattr(ak, CFFEX_SPOT[product])(symbol=symbol)


# -- the monitor ---------------------------------------------------------------------------------------------------

def percentile_of(value: float, history: pd.Series) -> float:
    history = history.dropna()
    return float((history <= value).mean()) if len(history) and np.isfinite(value) else float("nan")


@dataclass
class IndexState:
    index: VolIndex
    months: list[tuple[str, date]]     # (YYYY-MM, expiry) of the listed months, nearest first
    previous_close: float = float("nan")
    reference: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # daily values of the last years


class VolatilityMonitor:
    """Polled from the big-money monitor's loop (one process, one timer).  Writes data/monitor/volatility/<day>/
    minutes.parquet and status.json; alerts through ``on_alert(index, row, text)``."""

    def __init__(self, root: Path, config: VolConfig, day: date, states: list[IndexState], chains: SinaChains,
                 clock: Callable[[], datetime], on_alert: Callable[[VolIndex, dict, tuple[str, str]], None] | None = None):
        self.root, self.config, self.day, self.states, self.chains = root, config, day, states, chains
        self.clock, self.on_alert = clock, on_alert
        self.rows: list[dict] = []
        self.alerted: dict[str, str] = {}
        self.errors = 0
        self.skipped = 0
        self.out = root / "data" / "monitor" / "volatility" / day.isoformat()
        self.history_stamp: int | None = None

    def refresh_history(self) -> None:
        """Take the previous close and the reference values again when the daily history file changed: the morning
        catch-up recomputes it after its ingest, which on some days ends after the monitor starts (09:20)."""
        path = self.root / HISTORY_FILE
        stamp = path.stat().st_mtime_ns if path.is_file() else None
        if stamp is None or stamp == self.history_stamp:
            return
        try:
            history = load_history(self.root, self.config, self.day)
        except Exception as exc:  # a bad file must not stop the polling; the old values stay
            LOGGER.warning("volatility history not reloaded: %s", exc)
            return
        self.history_stamp = stamp
        for state in self.states:
            previous, reference = history[state.index.id]
            if previous != state.previous_close and not (np.isnan(previous) and np.isnan(state.previous_close)):
                LOGGER.info("volatility %s previous close %s -> %s", state.index.id, state.previous_close, previous)
            state.previous_close, state.reference = previous, reference

    def poll(self) -> list[dict]:
        self.refresh_history()
        now = self.clock()
        stamp = now.strftime("%H:%M")
        new = []
        for state in self.states:
            if state.index.exchange == "SSE" and SSE_CLOSING_AUCTION[0] <= now.time() < SSE_CLOSING_AUCTION[1]:
                continue
            try:
                expiries = dict((e, m) for m, e in state.months)
                terms = pick_terms(list(expiries), now, self.config.min_days)
                chains = {e: self.chains.chain(state.index, expiries[e]) for e in terms}
                row = {"time": stamp, "id": state.index.id, **index_value(chains, now, self.config)}
            except Exception as exc:  # one index failing must not stop the others
                self.errors += 1
                LOGGER.warning("volatility %s failed: %s", state.index.id, exc)
                continue
            thin = self._thin(row)
            if thin:
                self.skipped += 1
                LOGGER.info("volatility %s %s not kept: %s", state.index.id, stamp, thin)
                continue
            self.rows.append(row)
            new.append(row)
            self._check_alert(state, row)
        self.write()
        return new

    def _thin(self, row: dict) -> str | None:
        """Why a minute's quotes are too thin to keep (None when they are fine): a term without enough quotes, or
        far fewer strikes than usual today (the index then jumps on a few contracts)."""
        if not (np.isfinite(row["near_vol"]) and np.isfinite(row["next_vol"])):
            return f"a term without enough quotes ({row['strikes']} strikes)"
        usual = [r["strikes"] for r in self.rows if r["id"] == row["id"]]
        if len(usual) >= 5 and row["strikes"] < self.config.min_strike_share * float(np.median(usual)):
            return f"{row['strikes']} strikes, {np.median(usual):.0f} usually"
        return None

    def _check_alert(self, state: IndexState, row: dict) -> None:
        value, previous = row["value"], state.previous_close
        if state.index.id in self.alerted or not (np.isfinite(value) and np.isfinite(previous)):
            return
        rise = value / previous - 1
        pct = percentile_of(value, state.reference)
        if rise >= self.config.alert_rise and pct >= self.config.alert_percentile:
            self.alerted[state.index.id] = row["time"]
            if self.on_alert:
                self.on_alert(state.index, row, alert_text(state.index, row, previous, pct, self.config.history_years))

    def write(self, final: bool = False) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(self.rows, columns=MINUTE_COLUMNS)
        frame.to_parquet(self.out / "minutes.parquet", index=False)
        status = {"day": self.day.isoformat(), "updated": self.clock().isoformat(timespec="seconds"), "final": final,
                  "errors": self.errors, "skipped": self.skipped,
                  "alerts": [{"id": i, "time": t} for i, t in self.alerted.items()],
                  "indices": [{"id": s.index.id, "name": s.index.name, "exchange": s.index.exchange,
                               "underlying": s.index.underlying,
                               "previous_close": None if np.isnan(s.previous_close) else round(s.previous_close, 4)}
                              for s in self.states]}
        tmp = self.out / "status.json.tmp"
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.out / "status.json")


def alert_text(index: VolIndex, row: dict, previous: float, pct: float, years: int) -> tuple[str, str]:
    title = f"恐慌指数飙升：{index.name} {row['value']:.1f}（{row['time']}）"
    body = (f"较前一交易日收盘 {previous:.1f} 上升 {row['value'] / previous - 1:+.0%}，处于近 {years} 年第 "
            f"{pct * 100:.0f} 百分位。期权市场在为更大的波动定价，只是提示，不是交易信号。")
    return title, body


def load_history(root: Path, config: VolConfig, day: date) -> dict[str, tuple[float, pd.Series]]:
    """Per index: the last daily value before ``day`` (nan when none) and the values of the last
    ``history_years`` years (for the percentile)."""
    history_file = root / HISTORY_FILE
    history = pd.read_parquet(history_file) if history_file.is_file() else pd.DataFrame(
        columns=["id", "trade_date", "value"])
    history["trade_date"] = pd.to_datetime(history["trade_date"]).dt.date
    start = date(day.year - config.history_years, day.month, min(day.day, 28))
    out = {}
    for index in config.indices:
        own = history[(history["id"] == index.id) & (history["trade_date"] < day)].sort_values("trade_date")
        out[index.id] = (float(own["value"].iloc[-1]) if len(own) else float("nan"),
                         own.loc[own["trade_date"] >= start, "value"].astype(float).reset_index(drop=True))
    return out


def prepare(root: Path, config: VolConfig, day: date) -> list[IndexState]:
    """The listed months of each index and its daily history up to ``day``.  The months come from the canonical
    contracts file, not the catalog: the 09:05 catch-up may hold the catalog's lock when the monitor starts."""
    from ..data_platform.storage import read_canonical

    contracts = read_canonical(root, "option_contracts")
    if contracts is None or contracts.empty:
        raise FileNotFoundError("no option contracts stored (data/canonical/option_contracts)")
    contracts = contracts.assign(expiry=pd.to_datetime(contracts["expiry_date"]).dt.date)
    months = (contracts[contracts["expiry"] >= day].groupby(["exchange", "underlying", "expiry_month"], as_index=False)
              ["expiry"].min().sort_values("expiry"))
    history = load_history(root, config, day)
    states = []
    for index in config.indices:
        mine = months[(months["exchange"] == index.exchange) & (months["underlying"] == index.underlying)]
        previous, reference = history[index.id]
        states.append(IndexState(index, list(zip(mine["expiry_month"], mine["expiry"])), previous, reference))
    return states


# -- daily history ---------------------------------------------------------------------------------------------------

def daily_history(con: Any, config: VolConfig) -> pd.DataFrame:
    """Each index on each session from the stored daily bars: SSE closes (adjusted contracts left out), CFFEX
    settlement prices (the close when there is none).  Columns id, trade_date, value, near_vol, next_vol,
    near_expiry, next_expiry, strikes."""
    frames = []
    for index in config.indices:
        price = "coalesce(b.settle, b.close)" if index.exchange == "CFFEX" else "b.close"
        bars = con.execute(f"""
            SELECT b.trade_date, c.expiry_date, c.strike, c.kind, {price} AS price
            FROM option_daily_bars b JOIN option_contracts c USING (contract)
            WHERE c.exchange = ? AND c.underlying = ? AND NOT coalesce(c.adjusted, false) AND {price} > 0
            ORDER BY b.trade_date""", [index.exchange, index.underlying]).fetchdf()
        if bars.empty:
            continue
        bars = bars.assign(bid=bars["price"], ask=bars["price"])
        bars["expiry_date"] = pd.to_datetime(bars["expiry_date"]).dt.date
        rows = []
        for trade_date, day_bars in bars.groupby("trade_date", sort=True):
            now = datetime.combine(pd.Timestamp(trade_date).date(), EXPIRY_TIME)
            chains = {e: g for e, g in day_bars.groupby("expiry_date")}
            row = index_value(chains, now, config)
            if np.isfinite(row["value"]):
                rows.append({"id": index.id, "trade_date": pd.Timestamp(trade_date).date(), **row})
        frames.append(pd.DataFrame(rows))
    columns = ["id", "trade_date", "value", "near_vol", "next_vol", "near_expiry", "next_expiry", "strikes"]
    return pd.concat(frames, ignore_index=True)[columns] if frames else pd.DataFrame(columns=columns)


# -- backfill from stored minute bars ----------------------------------------------------------------------------------

def replay_minutes(minutes: pd.DataFrame, contracts: pd.DataFrame, config: VolConfig, day: date) -> pd.DataFrame:
    """The SSE indices of a past session from its stored 1-minute bars (data_platform/options.py): each contract's
    last trade price carried forward stands for its mid quote, so a stale deep out-of-the-money price makes it less
    exact than the live monitor.  CFFEX has no minute bars.  Columns as the live minutes.parquet."""
    if minutes is None or minutes.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    terms = contracts[(contracts["exchange"] == "SSE") & ~contracts["adjusted"].fillna(False).astype(bool)]
    terms = terms.assign(expiry_date=pd.to_datetime(terms["expiry_date"]).dt.date).set_index("contract")
    bars = minutes[minutes["contract"].isin(terms.index) & (minutes["price"] > 0)]
    if bars.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    prices = bars.assign(minute=bars["time"].astype(str).str[:5]).pivot_table(
        index="minute", columns="contract", values="price", aggfunc="last").sort_index().ffill()
    rows = []
    for index in config.indices:
        if index.exchange != "SSE":
            continue
        mine = terms[terms["underlying"] == index.underlying]
        cols = [c for c in prices.columns if c in mine.index]
        for minute, row in prices[cols].iterrows():
            quoted = row.dropna()
            if quoted.empty:
                continue
            frame = mine.loc[quoted.index, ["strike", "kind", "expiry_date"]].assign(bid=quoted.values, ask=quoted.values)
            now = datetime.combine(day, time(int(minute[:2]), int(minute[3:5])))
            chains = {e: g for e, g in frame.groupby("expiry_date")}
            rows.append({"time": minute, "id": index.id, **index_value(chains, now, config)})
    return pd.DataFrame(rows, columns=MINUTE_COLUMNS)
