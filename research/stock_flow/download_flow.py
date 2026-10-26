"""NOT USED: on 2026-10-25 Eastmoney refused these requests and then blocked the server for a while; the order-size
data was not downloaded (docs/research/stock-flow-2026-10.md).  Kept as the record of the attempt.

Daily money flow by order size (Eastmoney: 主力 = 超大单 + 大单, 中单, 小单; net inflow and its share of turnover)
of the CSI 300, CSI 500 and CSI 1000 constituents, about the last 120 sessions.  Question of 2026-10-25.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/download_flow.py data/market.duckdb \
        ~/L1/research/stock_flow/data

One request per stock (push2his fflow/daykline), 1 s apart; a stock already downloaded is skipped, so a stopped
run resumes.  Eastmoney refuses requests without a browser's User-Agent and Referer, and cuts a client off after
bursts: five failures in a row pause the run for ten minutes (at most three times).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
FIELDS = ["date", "main_net", "small_net", "medium_net", "large_net", "xlarge_net", "main_pct", "small_pct",
          "medium_pct", "large_pct", "xlarge_pct", "close", "pct_chg"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36", "Referer": "https://data.eastmoney.com/"}


def fetch(session: requests.Session, symbol: str) -> pd.DataFrame:
    market = 1 if symbol.startswith(("6", "9")) else 0
    params = {"lmt": 0, "klt": 101, "secid": f"{market}.{symbol}", "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
              "ut": "b2884a393a59ad64002292a3e90d46a5"}
    payload = session.get(URL, params=params, timeout=15).json()
    lines = (payload.get("data") or {}).get("klines") or []
    rows = [line.split(",")[:len(FIELDS)] for line in lines]
    frame = pd.DataFrame(rows, columns=FIELDS)
    for column in FIELDS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.insert(0, "symbol", symbol)
    return frame


def main() -> None:
    database, out = sys.argv[1], Path(sys.argv[2]).expanduser()
    (out / "stocks").mkdir(parents=True, exist_ok=True)
    with duckdb.connect(database, read_only=True) as con:
        members = con.execute("SELECT index_code, symbol FROM index_weights WHERE index_code IN ('000300', '000905', '000852') "
                              "AND as_of_date = (SELECT max(as_of_date) FROM index_weights w WHERE w.index_code = "
                              "index_weights.index_code)").fetchdf()
    members.to_csv(out / "members.csv", index=False)
    session = requests.Session()
    session.headers.update(HEADERS)
    todo = [s for s in members["symbol"].drop_duplicates() if not (out / "stocks" / f"{s}.parquet").exists()]
    print(f"{len(members)} memberships, {len(todo)} stocks to fetch", flush=True)
    failures = pauses = 0
    for i, symbol in enumerate(todo):
        try:
            frame = fetch(session, symbol)
            frame.to_parquet(out / "stocks" / f"{symbol}.parquet", index=False)
            failures = 0
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{symbol}: {type(exc).__name__} {str(exc)[:80]}", flush=True)
            if failures >= 5:
                if pauses >= 3:
                    print("too many refusals; stopping (run again later to resume)", flush=True)
                    break
                pauses += 1
                print("pausing 10 minutes", flush=True)
                time.sleep(600)
                session = requests.Session()
                session.headers.update(HEADERS)
                failures = 0
        if i % 100 == 0:
            print(f"{i}/{len(todo)}", flush=True)
        time.sleep(1.0)
    done = len(list((out / "stocks").glob("*.parquet")))
    (out / "status.json").write_text(json.dumps({"stocks": done, "memberships": len(members)}), encoding="utf-8")
    print(f"done: {done} stocks", flush=True)


if __name__ == "__main__":
    main()
