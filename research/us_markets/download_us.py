"""US index history for comparing with A-shares (question of 2026-10-24).  Sina via AKShare, daily bars.

    cd ~/L1/minerva-dev && .venv/bin/python ~/L1/research/us_markets/download_us.py ~/L1/research/us_markets/data

Indices: S&P 500 (.INX), Dow Jones (.DJI), Nasdaq Composite (.IXIC) from 2004; Nasdaq 100 (.NDX) and the
Philadelphia Semiconductor index (.SOX) from 2014; Nasdaq Golden Dragon China (.HXC, Chinese companies listed in
the US) from 2019; FTSE China A50 index futures in Singapore (CHA50CFD, continuous) from 2016.  Dates are the US
(or Singapore) session dates.  The VIX is not served by this source.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak
import pandas as pd

US = {".INX": "标普500", ".DJI": "道琼斯", ".IXIC": "纳斯达克综合", ".NDX": "纳斯达克100", ".SOX": "费城半导体",
      ".HXC": "纳斯达克中国金龙"}


def main() -> None:
    out = Path(sys.argv[1]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for symbol, name in US.items():
        raw = ak.index_us_stock_sina(symbol=symbol)
        frames.append(pd.DataFrame({"symbol": symbol, "name": name, "trade_date": pd.to_datetime(raw["date"]),
                                    "open": raw["open"], "high": raw["high"], "low": raw["low"],
                                    "close": raw["close"]}))
        print(symbol, name, len(raw), raw["date"].min(), raw["date"].max())
        time.sleep(1)
    raw = ak.futures_foreign_hist(symbol="CHA50CFD")
    frames.append(pd.DataFrame({"symbol": "CHA50CFD", "name": "富时中国A50期货", "trade_date": pd.to_datetime(raw["date"]),
                                "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"]}))
    print("CHA50CFD", len(raw), raw["date"].min(), raw["date"].max())
    pd.concat(frames, ignore_index=True).to_parquet(out / "us_indices.parquet", index=False)


if __name__ == "__main__":
    main()
