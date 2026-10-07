"""Fetch the Eastmoney parts the server could not reach (its quote hosts drop it now and then):
daily fund flow by order size, minute fund flow and 3-second trades of the latest session.
Writes to ./data_ws; the files are copied to the server's research directory afterwards."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_intraday import flow_days, flow_minutes, trades  # noqa: E402

SYMBOLS = ["510300", "510310", "510330", "159919", "515330", "510360", "515380", "510350"]
OUT = Path(__file__).resolve().parent / "data_ws"

OUT.mkdir(exist_ok=True)
for symbol in SYMBOLS:
    for name, fetch in (("flow_days", flow_days), ("flow_minutes", flow_minutes), ("trades", trades)):
        try:
            frame = fetch(symbol)
            frame.to_parquet(OUT / f"{name}_{symbol}.parquet")
            print(symbol, name, len(frame))
        except Exception as exc:  # noqa: BLE001
            print(symbol, name, "FAILED", exc)
        time.sleep(0.3)
