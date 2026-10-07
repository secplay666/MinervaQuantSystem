"""Third probe (2026-10-06): Sina money flow depth and paging, northbound after the 2024 change, and what main flow tells about price."""

from __future__ import annotations

import json
import time
import warnings

import pandas as pd
import requests

warnings.filterwarnings("ignore")
SINA = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/"}


def sina(method: str, **params) -> pd.DataFrame:
    response = requests.get(SINA + method, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    text = response.text.strip()
    return pd.DataFrame(json.loads(text) if text.startswith("[") else [])


def history(method: str, **params) -> pd.DataFrame:
    frames = []
    for page in range(1, 60):
        frame = sina(method, page=page, num=1000, sort="opendate", asc=0, **params)
        if frame.empty:
            break
        frames.append(frame)
        if len(frame) < 1000:
            break
        time.sleep(0.5)
    out = pd.concat(frames, ignore_index=True).rename(columns={"avg_changeratio": "changeratio"})
    out["opendate"] = pd.to_datetime(out["opendate"])
    for column in ("changeratio", "netamount", "r0_net", "r0_ratio", "turnover"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.sort_values("opendate").reset_index(drop=True)


def describe(name: str, frame: pd.DataFrame) -> None:
    flow, change = frame["r0_net"], frame["changeratio"]
    print(f"\n{name}: {len(frame)} sessions {frame['opendate'].iloc[0].date()} .. {frame['opendate'].iloc[-1].date()}")
    print(f"  main net inflow negative on {(flow < 0).mean():.0%} of days; all-size net negative on {(frame['netamount'] < 0).mean():.0%}")
    print(f"  Spearman main flow vs same-day change {flow.corr(change, method='spearman'):+.2f}, vs next day {flow.corr(change.shift(-1), method='spearman'):+.2f}")
    five = flow.rolling(5).sum()
    ahead5 = change[::-1].rolling(5).sum()[::-1].shift(-1)
    ahead20 = change[::-1].rolling(20).sum()[::-1].shift(-1)
    print(f"  5-day main flow vs next 5 days {five.corr(ahead5, method='spearman'):+.2f}, vs next 20 days {five.corr(ahead20, method='spearman'):+.2f}")
    up = change > 0
    print(f"  on up days main flow > 0 {(flow[up] > 0).mean():.0%}; on down days main flow < 0 {(flow[~up] < 0).mean():.0%}")


def main() -> None:
    started = time.time()
    stock = history("MoneyFlow.ssl_qsfx_zjlrqs", daima="sh600519")
    print(f"600519 history with num=1000: {len(stock)} rows in {time.time() - started:.1f}s")
    describe("600519 (Sina)", stock)

    started = time.time()
    sector = history("MoneyFlow.ssl_bkzj_zjlrqs", bankuai="0/new_blhy")
    print(f"\nnew_blhy sector history: {len(sector)} rows in {time.time() - started:.1f}s")
    describe("Sina sector new_blhy", sector)

    started = time.time()
    rows, page = [], 1
    while True:
        frame = sina("MoneyFlow.ssl_bkzj_ssggzj", page=page, num=1000, sort="r0_net", asc=0, bankuai="", shichang="")
        if frame.empty:
            break
        rows.append(frame)
        page += 1
        time.sleep(0.5)
    today = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    print(f"\nSina all-stock ranking: {len(today)} rows in {page - 1} pages, {time.time() - started:.1f}s; cols={list(today.columns)[:14]}")

    import akshare as ak

    north = ak.stock_hsgt_hist_em(symbol="北向资金")
    north["日期"] = pd.to_datetime(north["日期"])
    filled = north.dropna(subset=["当日成交净买额"])
    filled = filled[pd.to_numeric(filled["当日成交净买额"], errors="coerce") != 0]
    print(f"\nnorthbound: {len(north)} rows; daily net buy present until {filled['日期'].max().date()}")
    tail = north.tail(3)[["日期", "当日成交净买额", "买入成交额", "卖出成交额", "持股市值"]]
    print(tail.to_string(index=False))


if __name__ == "__main__":
    main()
