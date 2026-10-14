"""Download SSE/SZSE ETF option history for research (question of 2026-10-10: buying puts after the index
breaks down).  Research data only: it goes to OUT, not into the data platform.

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/options/download_options.py \
        ~/L1/research/options/data [--start 2015-02-09] [--end 2026-10-09] [--phase days|contracts|combine|all]

Phases (each resumable: finished items are skipped on a rerun):
  days       per trading day: SSE risk indicators of every listed contract (implied volatility, delta, gamma,
             theta, vega, rho), SSE daily statistics per underlying, SZSE daily statistics (from 2019-12-23)
  contracts  per SSE contract seen in the risk indicators: daily open/high/low/close/volume from Sina,
             including expired contracts; 300ETF and 50ETF first
  combine    one parquet per dataset, plus contract terms parsed from the contract code
Requests are paced (the SSE query service is the one the daily ETF-share step uses); failures are logged to
failures.jsonl and retried on the next run.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import duckdb
import pandas as pd

CALENDAR_DB = Path.home() / "L1/minerva-dev/data/market.duckdb"
SZSE_START = date(2019, 12, 23)
PRIORITY = ["510300", "510050", "510500", "588000", "588080"]
CONTRACT_RE = re.compile(r"^(\d{6})([CP])(\d{2})(\d{2})([A-Z])(\d{5})$")
PAUSE = {"sse": 0.8, "szse": 0.6, "sina": 0.3}


def sessions(start: date, end: date) -> list[date]:
    with duckdb.connect(str(CALENDAR_DB), read_only=True) as con:
        rows = con.execute("SELECT trade_date FROM trading_calendar WHERE is_open AND trade_date BETWEEN ? AND ? "
                           "ORDER BY 1", [start, end]).fetchall()
    return [r[0] for r in rows]


def fetch(kind: str, label: str, fn, failures: Path, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            time.sleep(PAUSE[kind])
            return result
        except Exception as exc:  # noqa: BLE001 (logged; the run goes on)
            error = f"{type(exc).__name__}: {exc}"[:300]
            time.sleep(PAUSE[kind] * 4 * attempt)
    with failures.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "kind": kind,
                                 "item": label, "error": error}, ensure_ascii=False) + "\n")
    return None


def save(frame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (frame if frame is not None else pd.DataFrame()).to_parquet(path, index=False)


def phase_days(out: Path, days: list[date], failures: Path) -> None:
    raw = out / "raw"
    for k, day in enumerate(days, 1):
        stamp = day.strftime("%Y%m%d")
        jobs = [("sse", raw / "risk_sse" / f"{stamp}.parquet", lambda: ak.option_risk_indicator_sse(date=stamp)),
                ("sse", raw / "stats_sse" / f"{stamp}.parquet", lambda: ak.option_daily_stats_sse(date=stamp))]
        if day >= SZSE_START:
            jobs.append(("szse", raw / "stats_szse" / f"{stamp}.parquet",
                         lambda: ak.option_daily_stats_szse(date=stamp)))
        for kind, path, fn in jobs:
            if path.exists():
                continue
            frame = fetch(kind, f"{path.parent.name}:{stamp}", fn, failures)
            if frame is not None:
                save(frame, path)
        if k % 50 == 0 or k == len(days):
            print(f"{datetime.now():%H:%M:%S} days {k}/{len(days)} ({day})", flush=True)


def contracts_seen(out: Path) -> pd.DataFrame:
    files = sorted((out / "raw" / "risk_sse").glob("*.parquet"))
    frames = [pd.read_parquet(f, columns=["SECURITY_ID", "CONTRACT_ID"]) for f in files if f.stat().st_size > 0]
    seen = pd.concat([f for f in frames if not f.empty]).drop_duplicates("SECURITY_ID")
    seen["underlying"] = seen["CONTRACT_ID"].str[:6]
    seen["rank"] = seen["underlying"].map({u: i for i, u in enumerate(PRIORITY)}).fillna(len(PRIORITY))
    return seen.sort_values(["rank", "CONTRACT_ID"])


def phase_contracts(out: Path, failures: Path) -> None:
    seen = contracts_seen(out)
    target = out / "raw" / "sina_daily"
    todo = [r for r in seen.itertuples(index=False) if not (target / f"{r.SECURITY_ID}.parquet").exists()]
    print(f"{datetime.now():%H:%M:%S} contracts: {len(seen)} seen, {len(todo)} to fetch", flush=True)
    for k, row in enumerate(todo, 1):
        frame = fetch("sina", f"sina:{row.SECURITY_ID}", lambda s=str(row.SECURITY_ID): ak.option_sse_daily_sina(symbol=s),
                      failures)
        if frame is not None:
            frame = frame.copy()
            frame.insert(0, "SECURITY_ID", str(row.SECURITY_ID))
            frame.insert(1, "CONTRACT_ID", row.CONTRACT_ID)
            save(frame, target / f"{row.SECURITY_ID}.parquet")
        if k % 200 == 0 or k == len(todo):
            print(f"{datetime.now():%H:%M:%S} contracts {k}/{len(todo)} ({row.CONTRACT_ID})", flush=True)


def terms(contract_ids: pd.Series) -> pd.DataFrame:
    rows = []
    for cid in contract_ids.unique():
        m = CONTRACT_RE.match(str(cid))
        rows.append({"CONTRACT_ID": cid, "underlying": m.group(1) if m else None,
                     "kind": {"C": "call", "P": "put"}.get(m.group(2)) if m else None,
                     "expiry_month": f"20{m.group(3)}-{m.group(4)}" if m else None,
                     "adjusted": (m.group(5) != "M") if m else None,
                     "strike": int(m.group(6)) / 1000 if m else None})
    return pd.DataFrame(rows)


def phase_combine(out: Path) -> None:
    for name in ("risk_sse", "stats_sse", "stats_szse", "sina_daily"):
        files = sorted((out / "raw" / name).glob("*.parquet"))
        frames = [pd.read_parquet(f) for f in files if f.stat().st_size > 0]
        frames = [f for f in frames if not f.empty]
        if not frames:
            continue
        frame = pd.concat(frames, ignore_index=True)
        frame.to_parquet(out / f"{name}.parquet", index=False)
        print(f"{name}: {len(files)} files, {len(frame):,} rows", flush=True)
    risk = out / "risk_sse.parquet"
    if risk.exists():
        ids = pd.read_parquet(risk, columns=["SECURITY_ID", "CONTRACT_ID"]).drop_duplicates("SECURITY_ID")
        table = ids.merge(terms(ids["CONTRACT_ID"]), on="CONTRACT_ID", how="left")
        table.to_parquet(out / "contracts_sse.parquet", index=False)
        print(f"contracts_sse: {len(table):,} contracts; unparsed codes {int(table['underlying'].isna().sum())}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--start", default="2015-02-09")
    parser.add_argument("--end", default=None, help="last session (default: the latest one before today)")
    parser.add_argument("--phase", default="all", choices=("days", "contracts", "combine", "all"))
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    failures = out / "failures.jsonl"
    end = date.fromisoformat(args.end) if args.end else None
    days = sessions(date.fromisoformat(args.start), end or date.today())
    if end is None:
        days = [d for d in days if d < date.today()]
    print(f"{datetime.now():%H:%M:%S} {len(days)} sessions {days[0]} .. {days[-1]}", flush=True)
    if args.phase in ("days", "all"):
        phase_days(out, days, failures)
    if args.phase in ("contracts", "all"):
        phase_contracts(out, failures)
    if args.phase in ("combine", "all"):
        phase_combine(out)
    print(f"{datetime.now():%H:%M:%S} done", flush=True)


if __name__ == "__main__":
    main()
