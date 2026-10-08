"""When does each data source publish a session's data, and does it change afterwards?  Question of 2026-10-08:
can the 18:17 daily run start earlier?

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/timing/probe_publication.py \
        ~/L1/research/timing --day 2026-10-09 --at 15:35,15:50,...,22:00 [--next 08:30,10:00]

Sleeps until each time (Asia/Shanghai) and probes a small sample through the same provider calls the daily
pipeline uses (about 35 requests per round), appending one JSON line per round to
``probes_<day>.jsonl``: for each item whether the day's row is there and its numbers, or the error.
Read-only; it writes nothing but that file.  ``--next`` adds times on the following calendar day (for
sources that publish the next morning).  ``timing_report.py`` compares the rounds.
"""

from __future__ import annotations

import argparse
import json
import time as clock
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from quant_system.data_platform.etf import etf_prefix
from quant_system.data_platform.providers.akshare_provider import AkShareProvider
from quant_system.data_platform.sessions import SHANGHAI_TZ, parse_hhmm

STOCKS = ["600000", "600519", "601318", "000001", "000333", "002415", "300750", "300059", "688981", "688111",
          "920961"]
INDICES = ["sh000001", "sh000300", "sz399006", "sh000852", "sh000688"]
CSINDEX = ["sh000510", "H00300", "H00905", "H00852"]
SW = ["801010", "801080", "801150"]
ETFS = ["510300", "159919", "588000"]
DATE_COLUMNS = ("date", "日期", "trade_date", "day")


def row_for(frame: pd.DataFrame, day: date) -> dict | None:
    if frame is None or frame.empty:
        return None
    column = next((c for c in DATE_COLUMNS if c in frame.columns), None)
    if column is None:
        return None
    match = frame[frame[column].astype(str).str[:10] == day.isoformat()]
    if match.empty:
        return None
    row = match.iloc[-1]
    return {str(k): (float(v) if isinstance(v, (int, float)) and pd.notna(v) else str(v)) for k, v in row.items()}


def probe(provider: AkShareProvider, day: date) -> dict:
    out: dict[str, dict] = {}
    start = (day - timedelta(days=14)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")

    def record(group: str, item: str, work) -> None:
        began = clock.perf_counter()
        try:
            value = work()
            entry = value if isinstance(value, dict) and "present" in value else {"present": value is not None,
                                                                                 "row": value}
        except Exception as exc:  # noqa: BLE001 (recorded, the round goes on)
            entry = {"present": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        entry["seconds"] = round(clock.perf_counter() - began, 2)
        out.setdefault(group, {})[item] = entry

    for symbol in STOCKS:
        record("daily_bars", symbol, lambda s=symbol: row_for(provider.fetch_daily_bars(s, start, end).frame, day))
    for symbol in INDICES:
        record("index", symbol, lambda s=symbol: row_for(provider.fetch_index_daily(s, start, end).frame, day))
    for symbol in CSINDEX:
        record("csindex", symbol, lambda s=symbol: row_for(provider.fetch_csindex_daily(s, start, end).frame, day))
    for code in SW:
        record("sw_index", code, lambda c=code: row_for(provider.fetch_sw_index_daily(c).frame, day))
    for symbol in ETFS:
        record("etf_bars", symbol,
               lambda s=symbol: row_for(provider.fetch_index_daily(etf_prefix(s) + s, start, end).frame, day))

    def sse_shares():
        frame = provider.fetch_etf_shares_sse(end)
        return {"present": frame is not None and not frame.empty, "rows": 0 if frame is None else len(frame)}

    def szse_shares():
        frame = provider.fetch_etf_shares_szse(end, end)
        rows = 0 if frame is None or frame.empty else int((frame["日期"].astype(str).str[:10] == day.isoformat()).sum()) \
            if "日期" in frame.columns else len(frame)
        return {"present": rows > 0, "rows": rows}

    def ticks():
        frame = provider.fetch_intraday_trades("sh510300")
        if frame is None or frame.empty:
            return {"present": False, "rows": 0}
        last = str(frame.iloc[-1].get("成交时间", ""))
        return {"present": True, "rows": len(frame), "last": last}

    def minutes():
        frame = provider.fetch_intraday_bars("sh000300")
        column = next((c for c in DATE_COLUMNS if c in frame.columns), None)
        part = frame[frame[column].astype(str).str[:10] == day.isoformat()] if column else frame.iloc[0:0]
        return {"present": not part.empty, "rows": len(part),
                "last": str(part.iloc[-1][column]) if not part.empty else None}

    record("etf_shares", "sse", sse_shares)
    record("etf_shares", "szse", szse_shares)
    record("intraday", "ticks_sh510300", ticks)
    record("intraday", "minutes_sh000300", minutes)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--day", required=True)
    parser.add_argument("--at", required=True, help="comma-separated HH:MM on the day")
    parser.add_argument("--next", default="", help="comma-separated HH:MM on the next calendar day")
    args = parser.parse_args()
    day = date.fromisoformat(args.day)
    times = [datetime.combine(day, parse_hhmm(t), SHANGHAI_TZ) for t in args.at.split(",") if t]
    times += [datetime.combine(day + timedelta(days=1), parse_hhmm(t), SHANGHAI_TZ) for t in args.next.split(",") if t]
    out = Path(args.out) / f"probes_{day.isoformat()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    provider = AkShareProvider(expected_version="1.18.78", max_retries=2, request_pause_seconds=0.35,
                               timeout_seconds=30)
    for at in sorted(times):
        wait = (at - datetime.now(SHANGHAI_TZ)).total_seconds()
        if wait < -300:
            print(f"{at:%m-%d %H:%M} already passed, skipped", flush=True)
            continue
        if wait > 0:
            clock.sleep(wait)
        began = datetime.now(SHANGHAI_TZ)
        try:
            items = probe(provider, day)
            error = None
        except Exception:  # noqa: BLE001
            items, error = {}, traceback.format_exc()[-500:]
        line = {"probe_at": began.isoformat(timespec="seconds"), "day": day.isoformat(), "items": items,
                "error": error, "seconds": round((datetime.now(SHANGHAI_TZ) - began).total_seconds(), 1)}
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        present = {group: sum(1 for v in values.values() if v.get("present")) for group, values in items.items()}
        print(f"{began:%m-%d %H:%M:%S} round done in {line['seconds']}s: {present}", flush=True)


if __name__ == "__main__":
    main()
