"""One real poll of the big-money monitor against Tencent for the last session (run on a non-trading day: the
endpoints still serve that session).  Reads production's catalog, writes to a scratch root.

    PYTHONPATH=<checkout>/src python live_smoke.py PROD_ROOT SCRATCH_ROOT CONFIG YYYY-MM-DD
"""

import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

from quant_system.monitor.big_money import BigMoneyMonitor, MonitorConfig, prepare

prod, scratch, config = Path(sys.argv[1]), Path(sys.argv[2]), MonitorConfig.load(Path(sys.argv[3]))
day = date.fromisoformat(sys.argv[4])
groups, profile = prepare(prod, config, day)
http = requests.Session()
http.headers["User-Agent"] = "Mozilla/5.0"
calls = [0]


def get(url, params):
    calls[0] += 1
    r = http.get(url, params=params, timeout=8)
    r.raise_for_status()
    return r.content.decode("gbk", errors="replace")


alerts = []
m = BigMoneyMonitor(scratch, config, groups, profile, day, get, lambda d, g, row: alerts.append((g.id, d, row["time"])),
                    clock=lambda: datetime.combine(day, datetime.strptime("15:00:30", "%H:%M:%S").time()))
started = time.time()
rows = m.poll()
first = time.time() - started
calls_first = calls[0]
started = time.time()
m.poll()
print(f"first poll {first:.1f}s, {calls_first} requests; second poll {time.time() - started:.1f}s, "
      f"{calls[0] - calls_first} requests; errors {m.errors}")
for g, r in zip(groups, rows):
    print(f"{g.name:8} {len(g.funds)} funds {'/'.join(f.symbol for f in g.funds):40} amount {r['amount'] / 1e8:6.1f}亿 "
          f"ratio {r['ratio']:.2f} big_net {r['big_net'] / 1e8:+6.1f}亿 share {r['big_share']:.0%} "
          f"premium {r['premium'] * 100:+.3f}% index {r['index_change'] * 100:+.2f}%")
print("alerts:", alerts)
