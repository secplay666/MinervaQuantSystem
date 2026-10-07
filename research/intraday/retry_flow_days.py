"""Slow retry for the daily fund flow (120 sessions) the throttled Eastmoney hosts refused earlier."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_intraday  # noqa: E402

fetch_intraday.get_json.__defaults__ = (1,)  # one attempt per call; this loop does the waiting
OUT = Path(__file__).resolve().parent / "data_ws"
WANTED = ["510300", "510310", "510330", "159919", "515330", "515380", "510350"]

for round_no in range(8):
    missing = [s for s in WANTED if not (OUT / f"flow_days_{s}.parquet").exists()]
    if not missing:
        break
    for symbol in missing:
        try:
            frame = fetch_intraday.flow_days(symbol)
            frame.to_parquet(OUT / f"flow_days_{symbol}.parquet")
            print(time.strftime("%H:%M"), symbol, len(frame), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(time.strftime("%H:%M"), symbol, "refused", str(exc)[:60], flush=True)
        time.sleep(45)
    time.sleep(120)
print("missing:", [s for s in WANTED if not (OUT / f"flow_days_{s}.parquet").exists()])
