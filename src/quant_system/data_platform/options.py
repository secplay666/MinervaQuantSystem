"""Exchange-traded options (期权): SSE ETF options and CFFEX index options.

Datasets
* ``option_contracts``   one row per contract: exchange, underlying, exchange code and name, call or put, expiry month
                         and date, strike, whether its terms were adjusted after a distribution (SSE), multiplier.
* ``option_daily_bars``  per contract and session (partitioned by underlying): open/high/low/close/volume; CFFEX adds
                         the amount, open interest, settlement price and the exchange's delta.
* ``option_risk``        SSE only (partitioned by underlying): the exchange's delta, gamma, theta, vega, rho and
                         implied volatility of each contract and session.
* ``option_minute_bars`` SSE only (partitioned by session): 1-minute bars of every listed contract, from the first
                         session collected on (the source keeps only the latest session).
* ``option_minute_totals`` SSE only: each contract's volume (contracts) and closing open interest of a session, summed
                         from its minute bars; the SSE daily bars carry no usable volume (below).
* ``option_fetch_log``   what has been fetched (not in the catalog).

Sources (checked 2026-10-11, research/options/)
* SSE risk indicators, one request per session back to 2015-02-09; they also list the session's contracts.
* SSE daily bars: Sina, one request per contract, its whole history (expired contracts too).  Its volume field is
  not a contract count and saturates at 2**31 - 1: on 2026-10-09 the 50ETF contracts' values summed to 62% of the
  exchange's total in shares, the minute volumes to 99.3% of it in contracts.  It is dropped.
* SSE minutes: Sina, the latest session only, so they are fetched before the next session opens.
* CFFEX: the exchange's daily statistics file of a session (all futures and options, back to 2019-12-23), one request
  per session.  No minute source is reachable for CFFEX options.
Expiry: SSE the fourth Wednesday of the month, CFFEX the third Friday; the next session when that day is closed.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .corporate import _numbers
from .normalization import _lineage, _to_date

SOURCE_SSE_RISK = "akshare.option_risk_indicator_sse"
SOURCE_SSE_DAILY = "akshare.option_sse_daily_sina"
SOURCE_SSE_MINUTE = "akshare.option_sse_minute_sina"
SOURCE_CFFEX_DAILY = "cffex.sj.hqsj.rtj"

SSE_UNDERLYINGS = {"510050": "上证50ETF", "510300": "沪深300ETF（华泰柏瑞）", "510500": "中证500ETF（南方）",
                   "588000": "科创50ETF（华夏）", "588080": "科创板50ETF（易方达）"}
CFFEX_PRODUCTS = {"IO": ("沪深300 股指期权", "sh000300"), "MO": ("中证1000 股指期权", "sh000852"),
                  "HO": ("上证50 股指期权", "sh000016")}
SSE_UNIT = 10_000
CFFEX_MULTIPLIER = 100
EMPTY_FINAL_DAYS = 3        # an empty session this recent may still be published; fetched again
MAX_CONSECUTIVE_FAILURES = 3

LINEAGE = ["source", "ingested_at", "run_id", "schema_version"]
CONTRACT_COLUMNS = ["contract", "exchange", "underlying", "code", "name", "kind", "expiry_month", "expiry_date",
                    "strike", "adjusted", "multiplier"] + LINEAGE
BAR_COLUMNS = ["contract", "underlying", "trade_date", "open", "high", "low", "close", "volume", "amount",
               "open_interest", "settle", "exchange_delta"] + LINEAGE
RISK_COLUMNS = ["contract", "underlying", "trade_date", "delta", "gamma", "theta", "vega", "rho", "iv"] + LINEAGE
MINUTE_COLUMNS = ["contract", "underlying", "trade_date", "time", "price", "volume", "open_interest",
                  "avg_price"] + LINEAGE
TOTAL_COLUMNS = ["contract", "underlying", "trade_date", "volume", "open_interest", "minutes"] + LINEAGE
LOG_COLUMNS = ["dataset", "window", "rows", "through", "run_id"]  # through: the latest final session then

SSE_CODE = re.compile(r"^(\d{6})([CP])(\d{2})(\d{2})([A-Z])(\d{5})$")
SSE_NAME_STRIKE = re.compile(r"(\d+)[A-Z]?$")
CFFEX_CODE = re.compile(r"^(IO|MO|HO)(\d{2})(\d{2})-([CP])-(\d+(?:\.\d+)?)$")


def expiry_day(month: str, exchange: str, open_dates: list[date]) -> date:
    """SSE: the fourth Wednesday of ``month`` (YYYY-MM); CFFEX: the third Friday; the next session if closed."""
    first = date(int(month[:4]), int(month[5:7]), 1)
    weekday, nth = (2, 4) if exchange == "SSE" else (4, 3)
    day = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    i = bisect_left(open_dates, day)
    return open_dates[i] if i < len(open_dates) and (open_dates[i] - day).days < 10 else day


def _frame(rows: list[dict], columns: list[str], source: str, run_id: str, ingested_at: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=[c for c in columns if c not in LINEAGE])
    return _lineage(frame, source, run_id, ingested_at).reindex(columns=columns)


# -- SSE ---------------------------------------------------------------------------------------------------------

def sse_contracts(raw: pd.DataFrame, open_dates: list[date], run_id: str, ingested_at: str) -> pd.DataFrame:
    """Contract terms from a session's risk indicators.  The strike is the one in the name ("50ETF购1月2755A"),
    which an adjusted contract changes while its code keeps the original."""
    if raw is None or raw.empty or "CONTRACT_ID" not in raw.columns:
        return pd.DataFrame(columns=CONTRACT_COLUMNS)
    rows = []
    for sid, code, name in raw[["SECURITY_ID", "CONTRACT_ID", "CONTRACT_SYMBOL"]].drop_duplicates("SECURITY_ID") \
            .itertuples(index=False):
        m = SSE_CODE.match(str(code).strip())
        if not m:
            continue
        month = f"20{m.group(3)}-{m.group(4)}"
        named = SSE_NAME_STRIKE.search(str(name).strip())
        strike = int(named.group(1)) / 1000 if named else int(m.group(6)) / 1000
        adjusted = m.group(5) != "M"
        rows.append({"contract": str(sid).strip(), "exchange": "SSE", "underlying": m.group(1), "code": str(code).strip(),
                     "name": str(name).strip(), "kind": "call" if m.group(2) == "C" else "put", "expiry_month": month,
                     "expiry_date": expiry_day(month, "SSE", open_dates), "strike": strike, "adjusted": adjusted,
                     "multiplier": np.nan if adjusted else float(SSE_UNIT)})
    return _frame(rows, CONTRACT_COLUMNS, SOURCE_SSE_RISK, run_id, ingested_at)


def normalize_sse_risk(raw: pd.DataFrame, run_id: str, ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty or "SECURITY_ID" not in raw.columns:
        return pd.DataFrame(columns=RISK_COLUMNS)
    frame = pd.DataFrame({
        "contract": raw["SECURITY_ID"].astype(str).str.strip(),
        "underlying": raw["CONTRACT_ID"].astype(str).str.strip().str[:6],
        "trade_date": _to_date(raw["TRADE_DATE"]),
        "delta": _numbers(raw, "DELTA_VALUE"), "gamma": _numbers(raw, "GAMMA_VALUE"),
        "theta": _numbers(raw, "THETA_VALUE"), "vega": _numbers(raw, "VEGA_VALUE"), "rho": _numbers(raw, "RHO_VALUE"),
        "iv": _numbers(raw, "IMPLC_VOLATLTY"),
    })
    frame.loc[frame["iv"] <= 0, "iv"] = np.nan  # the exchange reports 0 where no volatility solves the price
    frame = frame[frame["trade_date"].notna()]
    return _lineage(frame, SOURCE_SSE_RISK, run_id, ingested_at).reindex(columns=RISK_COLUMNS)


def normalize_sse_daily(raw: pd.DataFrame, contract: str, underlying: str, run_id: str,
                        ingested_at: str) -> pd.DataFrame:
    if raw is None or raw.empty or "日期" not in raw.columns:
        return pd.DataFrame(columns=BAR_COLUMNS)
    frame = pd.DataFrame({"trade_date": _to_date(raw["日期"]), "open": _numbers(raw, "开盘"), "high": _numbers(raw, "最高"),
                          "low": _numbers(raw, "最低"), "close": _numbers(raw, "收盘")})
    frame["volume"] = np.nan  # the source's 成交量 is unusable (module docstring); see option_minute_totals
    for column in ("open", "high", "low", "close"):
        frame.loc[frame[column] <= 0, column] = np.nan  # Sina lacks the closes of February 2015
    frame.insert(0, "underlying", underlying)
    frame.insert(0, "contract", contract)
    frame = frame[frame["trade_date"].notna()]
    return _lineage(frame, SOURCE_SSE_DAILY, run_id, ingested_at).reindex(columns=BAR_COLUMNS)


def normalize_sse_minutes(raw: pd.DataFrame, contract: str, underlying: str, day: date, run_id: str,
                          ingested_at: str) -> pd.DataFrame:
    """The source's latest session; kept only when it is ``day`` (fetched before the next session opened)."""
    if raw is None or raw.empty or "时间" not in raw.columns:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    frame = pd.DataFrame({"trade_date": _to_date(raw["日期"]), "time": raw["时间"].astype(str).str.strip(),
                          "price": _numbers(raw, "价格"), "volume": _numbers(raw, "成交"),
                          "open_interest": _numbers(raw, "持仓"), "avg_price": _numbers(raw, "均价")})
    frame = frame[frame["trade_date"] == day]
    for column in ("price", "avg_price"):
        frame.loc[frame[column] <= 0, column] = np.nan  # 0 before the first trade
    frame.insert(0, "underlying", underlying)
    frame.insert(0, "contract", contract)
    return _lineage(frame, SOURCE_SSE_MINUTE, run_id, ingested_at).reindex(columns=MINUTE_COLUMNS)


def minutes_moved_on(raw: pd.DataFrame, day: date) -> bool:
    """Whether the source already serves a later session than ``day`` (the next one has opened)."""
    if raw is None or raw.empty or "日期" not in raw.columns:
        return False
    dates = _to_date(raw["日期"]).dropna()
    return not dates.empty and bool((dates > day).all())


def minute_totals(minutes: pd.DataFrame) -> pd.DataFrame:
    """Per contract and session: the volume summed over the minutes and the last open interest."""
    if minutes is None or minutes.empty:
        return pd.DataFrame(columns=TOTAL_COLUMNS)
    ordered = minutes.sort_values(["contract", "trade_date", "time"])
    grouped = ordered.groupby(["contract", "trade_date"], sort=True)
    totals = grouped.agg(underlying=("underlying", "first"), volume=("volume", "sum"),
                         open_interest=("open_interest", "last"), minutes=("time", "size"),
                         source=("source", "last"), ingested_at=("ingested_at", "last"), run_id=("run_id", "last"),
                         schema_version=("schema_version", "last")).reset_index()
    return totals.reindex(columns=TOTAL_COLUMNS)


# -- CFFEX -------------------------------------------------------------------------------------------------------

def normalize_cffex_daily(raw: pd.DataFrame, day: date, open_dates: list[date], run_id: str,
                          ingested_at: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(contracts, bars) of the option rows of a CFFEX daily statistics file (futures rows are skipped)."""
    empty = (pd.DataFrame(columns=CONTRACT_COLUMNS), pd.DataFrame(columns=BAR_COLUMNS))
    if raw is None or raw.empty or "合约代码" not in raw.columns:
        return empty
    codes = raw["合约代码"].astype(str).str.strip()
    parsed = codes.str.extract(CFFEX_CODE)
    keep = parsed[0].notna()
    if not keep.any():
        return empty
    raw, codes, parsed = raw[keep], codes[keep], parsed[keep]
    months = "20" + parsed[1] + "-" + parsed[2]
    contracts = pd.DataFrame({
        "contract": codes, "exchange": "CFFEX", "underlying": parsed[0], "code": codes, "name": codes,
        "kind": parsed[3].map({"C": "call", "P": "put"}), "expiry_month": months,
        "expiry_date": [expiry_day(m, "CFFEX", open_dates) for m in months],
        "strike": pd.to_numeric(parsed[4], errors="coerce"), "adjusted": False, "multiplier": float(CFFEX_MULTIPLIER)})
    contracts = _lineage(contracts, SOURCE_CFFEX_DAILY, run_id, ingested_at).reindex(columns=CONTRACT_COLUMNS)
    bars = pd.DataFrame({
        "contract": codes, "underlying": parsed[0], "trade_date": day,
        "open": _numbers(raw, "今开盘"), "high": _numbers(raw, "最高价"), "low": _numbers(raw, "最低价"),
        "close": _numbers(raw, "今收盘"), "volume": _numbers(raw, "成交量"),
        "amount": _numbers(raw, "成交金额") * 1e4,  # the file counts in 10,000 yuan
        "open_interest": _numbers(raw, "持仓量"), "settle": _numbers(raw, "今结算"), "exchange_delta": _numbers(raw, "Delta")})
    for column in ("open", "high", "low", "close"):
        bars.loc[bars[column] <= 0, column] = np.nan
    bars = _lineage(bars, SOURCE_CFFEX_DAILY, run_id, ingested_at).reindex(columns=BAR_COLUMNS)
    return contracts.reset_index(drop=True), bars.reset_index(drop=True)


# -- merging ------------------------------------------------------------------------------------------------------

def merge_rows(existing: pd.DataFrame | None, incoming: pd.DataFrame, keys: list[str], columns: list[str],
               order: list[str]) -> pd.DataFrame:
    """Later rows win on ``keys`` (a contract's refetched history replaces what was stored)."""
    parts = [p.reindex(columns=columns) for p in (existing, incoming) if p is not None and not p.empty]
    if not parts:
        return pd.DataFrame(columns=columns)
    frame = pd.concat(parts, ignore_index=True)
    return frame.drop_duplicates(keys, keep="last").sort_values(order, ignore_index=True)


def merge_contracts(existing, incoming):
    return merge_rows(existing, incoming, ["contract"], CONTRACT_COLUMNS, ["exchange", "underlying", "expiry_month",
                                                                          "kind", "strike", "contract"])


def merge_bars(existing, incoming):
    return merge_rows(existing, incoming, ["contract", "trade_date"], BAR_COLUMNS, ["contract", "trade_date"])


def merge_risk(existing, incoming):
    return merge_rows(existing, incoming, ["contract", "trade_date"], RISK_COLUMNS, ["contract", "trade_date"])


def merge_totals(existing, incoming):
    return merge_rows(existing, incoming, ["contract", "trade_date"], TOTAL_COLUMNS, ["trade_date", "contract"])


def merge_minutes(existing, incoming):
    return merge_rows(existing, incoming, ["contract", "trade_date", "time"], MINUTE_COLUMNS,
                      ["contract", "trade_date", "time"])


# -- what to fetch ------------------------------------------------------------------------------------------------

def log_row(dataset: str, window: str, rows: int, run_id: str, through: date, day: date | None = None) -> dict | None:
    """Fetch-log entry, or None while an empty recent session may still be published."""
    if day is not None and rows == 0 and (through - day).days <= EMPTY_FINAL_DAYS:
        return None
    return {"dataset": dataset, "window": window, "rows": rows, "through": through, "run_id": run_id}


def sessions_todo(sessions: list[date], start: date, log: pd.DataFrame | None, dataset: str) -> list[date]:
    """Sessions from ``start`` not in the fetch log yet, oldest first."""
    done = set() if log is None or log.empty else set(log.loc[log["dataset"] == dataset, "window"])
    return [d for d in sessions if d >= start and d.isoformat() not in done]


def sse_daily_todo(contracts: pd.DataFrame | None, log: pd.DataFrame | None, latest: date) -> list[str]:
    """SSE contracts whose daily bars to (re)fetch.  A request returns a contract's whole history, so a contract is
    due until it has been fetched once its last session to date (``latest``, or its expiry) was over: every listed
    contract each new session, an expired one once more after it expired.  Oldest underlying first."""
    if contracts is None or contracts.empty:
        return []
    sse = contracts[contracts["exchange"] == "SSE"]
    expiry = pd.to_datetime(sse["expiry_date"])
    target = expiry.where(expiry < pd.Timestamp(latest), pd.Timestamp(latest))
    reached = pd.Series(pd.NaT, index=sse.index, dtype="datetime64[ns]")
    if log is not None and not log.empty:
        fetched = log[log["dataset"] == "sse_daily"]
        through = pd.to_datetime(fetched["through"]).groupby(fetched["window"].to_numpy()).max()
        reached = pd.to_datetime(sse["contract"].map(through))
    todo = sse[reached.isna() | (reached < target)]
    order = {u: i for i, u in enumerate(SSE_UNDERLYINGS)}
    todo = todo.assign(rank=todo["underlying"].map(order).fillna(len(order)))
    return list(todo.sort_values(["rank", "expiry_month", "contract"])["contract"])


def merge_log(existing: pd.DataFrame | None, rows: list[dict]) -> pd.DataFrame:
    incoming = pd.DataFrame(rows, columns=LOG_COLUMNS)
    return merge_rows(existing, incoming, ["dataset", "window"], LOG_COLUMNS, ["dataset", "window"])
