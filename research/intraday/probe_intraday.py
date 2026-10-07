"""Probe which intraday sources work from here and how much recent history they keep (2026-10-04).

Prints one line per source: rows, first and last timestamps, columns.  Nothing is stored.
"""

from __future__ import annotations

import json
import sys
import time
import traceback

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}


def show(name: str, fetch) -> None:
    started = time.time()
    try:
        frame = fetch()
        if isinstance(frame, dict):
            frame = pd.DataFrame(frame)
        if frame is None or len(frame) == 0:
            print(f"{name:42s} EMPTY ({time.time() - started:.1f}s)")
            return
        cols = list(frame.columns)
        time_col = next((c for c in cols if any(k in str(c) for k in ("时间", "day", "date", "time", "日期"))), cols[0])
        first, last = frame[time_col].iloc[0], frame[time_col].iloc[-1]
        print(f"{name:42s} OK rows={len(frame):6d} {first} .. {last} ({time.time() - started:.1f}s) cols={cols[:9]}")
    except Exception as exc:  # noqa: BLE001 - a probe reports every failure
        print(f"{name:42s} FAIL {type(exc).__name__}: {str(exc)[:110]} ({time.time() - started:.1f}s)")


def tencent_minutes(code: str, count: int = 2000) -> pd.DataFrame:
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m1,,{count}"
    data = requests.get(url, headers=UA, timeout=20).json()["data"][code]["m1"]
    return pd.DataFrame(data, columns=["time", "open", "close", "high", "low", "volume", "x", "y"][:len(data[0])])


def em_fflow_minutes(secid: str) -> pd.DataFrame:
    url = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&secid={secid}")
    lines = requests.get(url, headers=UA, timeout=20).json()["data"]["klines"]
    return pd.DataFrame([line.split(",") for line in lines]).rename(columns={0: "time"})


def em_fflow_days(secid: str) -> pd.DataFrame:
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&secid={secid}")
    lines = requests.get(url, headers=UA, timeout=20).json()["data"]["klines"]
    return pd.DataFrame([line.split(",") for line in lines]).rename(columns={0: "date"})


def main() -> None:
    import akshare as ak

    print("akshare", ak.__version__)
    for code, prefixed, secid in (("510300", "sh510300", "1.510300"), ("159919", "sz159919", "0.159919")):
        print(f"--- {code}")
        show("em fund_etf_hist_min_em 1m", lambda: ak.fund_etf_hist_min_em(
            symbol=code, period="1", adjust="", start_date="2026-09-01 09:30:00", end_date="2026-09-30 15:00:00"))
        show("em fund_etf_hist_min_em 5m", lambda: ak.fund_etf_hist_min_em(
            symbol=code, period="5", adjust="", start_date="2026-01-01 09:30:00", end_date="2026-09-30 15:00:00"))
        show("sina stock_zh_a_minute 1m", lambda: ak.stock_zh_a_minute(symbol=prefixed, period="1", adjust=""))
        show("sina stock_zh_a_minute 5m", lambda: ak.stock_zh_a_minute(symbol=prefixed, period="5", adjust=""))
        show("tencent mkline 1m", lambda: tencent_minutes(prefixed))
        for day in ("20260930", "20260929", "20260924", "20260915"):
            show(f"sina stock_intraday_sina {day}", lambda day=day: ak.stock_intraday_sina(symbol=prefixed, date=day))
        show("em stock_intraday_em (today)", lambda: ak.stock_intraday_em(symbol=code))
        show("tencent stock_zh_a_tick_tx_js", lambda: ak.stock_zh_a_tick_tx_js(symbol=prefixed))
        show("em fund flow minutes (today)", lambda: em_fflow_minutes(secid))
        show("em fund flow days", lambda: em_fflow_days(secid))
    print("--- index and futures")
    show("sina index minute sh000300 1m", lambda: ak.stock_zh_a_minute(symbol="sh000300", period="1", adjust=""))
    show("sina futures IF2510 1m", lambda: ak.futures_zh_minute_sina(symbol="IF2510", period="1"))
    show("sina futures IF0 1m", lambda: ak.futures_zh_minute_sina(symbol="IF0", period="1"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
