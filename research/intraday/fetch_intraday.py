"""Fetch what intraday history exists for the CSI 300 ETFs (research, 2026-10-04).

Run on the server in ~/L1/research/intraday; writes parquet files to ./data.  Sources (see probe_intraday*.py):
* Eastmoney daily fund flow by order size (~120 sessions), minute fund flow and 3-second trades of the latest session;
* Sina 1-minute (~8 sessions) and 5-minute (~2 months) bars, the CSI 300 index 1-minute bars, IF main contract minutes.
"""

from __future__ import annotations

import glob
import json
import time
import os
from pathlib import Path

import pandas as pd
import requests

SCRATCH = Path(os.environ.get("MINERVA_DATA", Path.home() / "L1" / "minerva-dev" / "data")) / "canonical"
OUT = Path(__file__).resolve().parent / "data"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Referer": "https://quote.eastmoney.com/"}
FLOW_FIELDS = ["main", "small", "medium", "large", "super"]  # net inflow (yuan): main = large + super


def read(dataset: str) -> pd.DataFrame:
    return pd.concat(pd.read_parquet(f) for f in glob.glob(str(SCRATCH / dataset / "**" / "*.parquet"), recursive=True))


def get_json(url: str, attempts: int = 6) -> dict:
    for k in range(attempts):
        try:
            return requests.get(url, headers=UA, timeout=20).json()
        except Exception:  # noqa: BLE001 - the quote hosts drop connections now and then
            time.sleep(2 + 2 * k)
    raise RuntimeError(f"gave up: {url[:90]}")


def secid(symbol: str) -> str:
    return ("1." if symbol.startswith(("5", "6")) else "0.") + symbol


def flow_days(symbol: str) -> pd.DataFrame:
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&secid={secid(symbol)}")
    rows = [line.split(",") for line in get_json(url)["data"]["klines"]]
    frame = pd.DataFrame([r[:6] + [r[11], r[12]] for r in rows], columns=["date", *FLOW_FIELDS, "close", "change_pct"])
    frame[FLOW_FIELDS + ["close", "change_pct"]] = frame[FLOW_FIELDS + ["close", "change_pct"]].astype(float)
    return frame.assign(symbol=symbol, date=pd.to_datetime(frame["date"]).dt.date)


def flow_minutes(symbol: str) -> pd.DataFrame:
    url = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56&secid={secid(symbol)}")
    rows = [line.split(",") for line in get_json(url)["data"]["klines"]]
    frame = pd.DataFrame(rows, columns=["time", *FLOW_FIELDS])
    frame[FLOW_FIELDS] = frame[FLOW_FIELDS].astype(float)  # cumulative through the session
    return frame.assign(symbol=symbol)


def trades(symbol: str) -> pd.DataFrame:
    url = (f"https://push2.eastmoney.com/api/qt/stock/details/get?secid={secid(symbol)}&fields1=f1,f2,f3,f4"
           "&fields2=f51,f52,f53,f54,f55&pos=-1000000")
    data = get_json(url)["data"]
    rows = [line.split(",") for line in data["details"]]
    frame = pd.DataFrame(rows, columns=["time", "price", "lots", "count", "side"])
    frame[["price", "lots"]] = frame[["price", "lots"]].astype(float)
    return frame.assign(symbol=symbol)


def sina_bars(code: str, scale: int) -> pd.DataFrame:
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData"
           f"?symbol={code}&scale={scale}&ma=no&datalen=1970")
    text = requests.get(url, headers={**UA, "Referer": "https://finance.sina.com.cn/"}, timeout=20).text
    frame = pd.DataFrame(json.loads(text[text.index("(") + 1:text.rindex(")")]))
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in frame:
            frame[col] = frame[col].astype(float)
    return frame.assign(code=code)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    master, shares, bars = read("etf_master"), read("etf_shares"), read("etf_bars")
    csi300 = master[master["index_code"].isin(["000300", "399300"])].drop_duplicates("symbol", keep="last")
    latest_shares = shares.sort_values("trade_date").drop_duplicates("symbol", keep="last").set_index("symbol")["shares"]
    latest_close = bars.sort_values("trade_date").drop_duplicates("symbol", keep="last").set_index("symbol")["close"]
    csi300 = csi300.assign(aum=csi300["symbol"].map(latest_shares) * csi300["symbol"].map(latest_close))
    top = csi300.dropna(subset=["aum"]).sort_values("aum", ascending=False).head(8)
    print(top[["symbol", "name", "aum"]].assign(aum=lambda f: (f["aum"] / 1e8).round(0)).to_string(index=False))
    top[["symbol", "name", "exchange", "aum"]].to_parquet(OUT / "etfs.parquet")
    for symbol in top["symbol"]:
        code = ("sh" if symbol.startswith("5") else "sz") + symbol
        for name, fetch in (("flow_days", lambda: flow_days(symbol)), ("flow_minutes", lambda: flow_minutes(symbol)),
                            ("trades", lambda: trades(symbol)), ("bars_1m", lambda: sina_bars(code, 1)),
                            ("bars_5m", lambda: sina_bars(code, 5))):
            target = OUT / f"{name}_{symbol}.parquet"
            if target.exists():
                continue  # fetched by an earlier attempt
            try:
                frame = fetch()
                frame.to_parquet(target)
                print(f"{symbol} {name}: {len(frame)} rows", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"{symbol} {name}: FAILED {exc}", flush=True)
            time.sleep(0.5)
    sina_bars("sh000300", 1).to_parquet(OUT / "index_1m.parquet")
    import akshare as ak

    ak.futures_zh_minute_sina(symbol="IF0", period="1").to_parquet(OUT / "if_1m.parquet")
    shares[shares["symbol"].isin(top["symbol"])].to_parquet(OUT / "shares.parquet")
    bars[bars["symbol"].isin(top["symbol"])].to_parquet(OUT / "etf_daily.parquet")
    print("saved to", OUT)


if __name__ == "__main__":
    main()
