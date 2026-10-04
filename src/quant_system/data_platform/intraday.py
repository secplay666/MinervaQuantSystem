"""Intraday data: CSI 300 ETFs first (the 日内 tab of the ETF page).

Free sources keep little history, so this is collected every evening:

* 1-minute bars (Sina keeps about eight sessions) of the ETFs and the CSI 300 index, and of the IF
  main contract: re-fetched every run, so a session missed within a week is filled in;
* 3-second trades with the active side (Tencent; the latest session only, and without a date): stored
  only when they agree with that session's 1-minute bars, so trades are never filed under another day.

A session counts only once its last bar is from 14:59 on (a fetch during trading carries a partial day);
the rebuild applies the same rule to the raw responses.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .normalization import _lineage

SOURCE_BARS = "sina.CN_MarketDataService.getKLineData.1m"
SOURCE_FUTURES = "akshare.futures_zh_minute_sina"
SOURCE_TRADES = "akshare.stock_zh_a_tick_tx_js.tencent"
BAR_COLUMNS = ["trade_date", "symbol", "time", "open", "high", "low", "close", "volume", "amount", "hold",
               "source", "ingested_at", "run_id", "schema_version"]
TRADE_COLUMNS = ["trade_date", "symbol", "seq", "time", "price", "volume", "amount", "side",
                 "source", "ingested_at", "run_id", "schema_version"]
SIDES = {"买盘": "B", "卖盘": "S", "中性盘": "N"}
SESSION_END = "14:59"     # the last bar of a complete session is at least this late
MAX_AMOUNT_GAP = 0.02     # trades and bars of the same session agree within this
INDEX_PREFIXES = ("sh000", "sz399")


def symbol_of(code: str) -> str:
    """How a code is stored: funds by their six digits, indices with the exchange prefix, futures as given."""
    if code[:2] in ("sh", "sz") and not code.startswith(INDEX_PREFIXES):
        return code[2:]
    return code


def has_trades(code: str) -> bool:
    """Funds have trades; indices and futures only bars."""
    return code[:2] in ("sh", "sz") and not code.startswith(INDEX_PREFIXES)


def normalize_bars(raw: pd.DataFrame, code: str, last_day: date | None, run_id: str, ingested_at: str,
                   source: str) -> pd.DataFrame:
    """Complete sessions on or before ``last_day`` (None: no limit, as in a rebuild)."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    stamp = pd.to_datetime(raw["day" if "day" in raw.columns else "datetime"])

    def number(column: str) -> pd.Series:
        return pd.to_numeric(raw[column], errors="coerce") if column in raw.columns else pd.Series(float("nan"),
                                                                                                    index=raw.index)

    frame = pd.DataFrame({"trade_date": stamp.dt.date, "symbol": symbol_of(code), "time": stamp.dt.strftime("%H:%M"),
                          "open": number("open"), "high": number("high"), "low": number("low"),
                          "close": number("close"), "volume": number("volume"), "amount": number("amount"),
                          "hold": number("hold")})
    if last_day is not None:
        frame = frame[frame["trade_date"] <= last_day]
    complete = frame.groupby("trade_date")["time"].transform("max") >= SESSION_END
    frame = _lineage(frame[complete].copy(), source, run_id, ingested_at)
    return frame.drop_duplicates(["trade_date", "symbol", "time"], keep="last")[BAR_COLUMNS].reset_index(drop=True)


def normalize_trades(raw: pd.DataFrame, code: str, day: date, run_id: str, ingested_at: str) -> pd.DataFrame:
    """Tencent 3-second trades: volume in lots of 100, amount in yuan, side 买盘/卖盘/中性盘."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.DataFrame({
        "trade_date": day, "symbol": symbol_of(code), "seq": range(len(raw)),
        "time": raw["成交时间"].astype(str), "price": pd.to_numeric(raw["成交价格"], errors="coerce"),
        "volume": pd.to_numeric(raw["成交量"], errors="coerce") * 100,
        "amount": pd.to_numeric(raw["成交金额"], errors="coerce"),
        "side": raw["性质"].map(SIDES).fillna("N"),
    })
    return _lineage(frame, SOURCE_TRADES, run_id, ingested_at)[TRADE_COLUMNS]


def check_trades(trades: pd.DataFrame, bars: pd.DataFrame) -> str | None:
    """None when the trades are the session of ``bars`` (one symbol, one day), else why not."""
    if trades.empty:
        return "没有逐笔数据"
    if bars.empty:
        return "没有这一天的分钟线，无法确认逐笔属于哪一天"
    bar_amount, trade_amount = float(bars["amount"].sum()), float(trades["amount"].sum())
    if bar_amount <= 0 or abs(trade_amount / bar_amount - 1) > MAX_AMOUNT_GAP:
        return f"逐笔成交额 {trade_amount / 1e8:.2f} 亿与分钟线 {bar_amount / 1e8:.2f} 亿不符"
    last_close = float(bars.sort_values("time")["close"].iloc[-1])
    if abs(float(trades["price"].iloc[-1]) - last_close) > 0.0015:
        return f"逐笔最后成交价 {trades['price'].iloc[-1]} 与分钟线收盘 {last_close} 不符"
    return None


def merge_day(existing: pd.DataFrame | None, incoming: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """A day's partition with the incoming symbols' rows replaced."""
    if existing is None or existing.empty:
        return incoming.reindex(columns=columns).reset_index(drop=True)
    kept = existing[~existing["symbol"].isin(set(incoming["symbol"]))]
    order = ["symbol", "seq"] if "seq" in columns else ["symbol", "time"]
    return pd.concat([kept, incoming], ignore_index=True).reindex(columns=columns).sort_values(order, ignore_index=True)
