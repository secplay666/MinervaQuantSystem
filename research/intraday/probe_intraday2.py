"""Probe 2: how far back Sina minute bars go when asked directly, and Eastmoney from this network."""

from __future__ import annotations

import json
import sys

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Referer": "https://finance.sina.com.cn/"}


def sina(code: str, scale: int, datalen: int) -> None:
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
           f"?symbol={code}&scale={scale}&ma=no&datalen={datalen}")
    try:
        text = requests.get(url, headers=UA, timeout=20).text
        rows = json.loads(text[text.index("(") + 1:text.rindex(")")])
        print(f"sina {code} scale={scale:3d} datalen={datalen:6d}: {len(rows):6d} bars {rows[0]['day']} .. {rows[-1]['day']}")
    except Exception as exc:  # noqa: BLE001
        print(f"sina {code} scale={scale} datalen={datalen}: FAIL {type(exc).__name__}: {str(exc)[:100]}")


def em(url: str, name: str) -> None:
    try:
        payload = requests.get(url, headers=UA, timeout=15).json()
        lines = (payload.get("data") or {}).get("klines") or (payload.get("data") or {}).get("details") or []
        print(f"em {name}: {len(lines)} lines {lines[:1]} .. {lines[-1:]}")
    except Exception as exc:  # noqa: BLE001
        print(f"em {name}: FAIL {type(exc).__name__}: {str(exc)[:100]}")


for scale, datalen in ((1, 1970), (1, 5000), (1, 20000), (5, 20000), (15, 20000), (30, 20000)):
    sina("sh510300", scale, datalen)
if "--em" in sys.argv:
    em("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.510300&klt=1&fqt=0&lmt=100000&end=20500101"
       "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57", "1m kline")
    em("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101&secid=0.159919"
       "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65", "fund flow days 159919")
    em("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid=1.510300"
       "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65", "fund flow minutes")
    em("https://push2.eastmoney.com/api/qt/stock/details/get?secid=1.510300&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55"
       "&pos=-1000000", "ticks (latest day)")
