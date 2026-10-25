"""Trial session of the big-money monitor before it is deployed: waits until 09:20, reads production's catalog,
writes to a scratch root, prints alerts instead of recording events.

    setsid nohup env PYTHONPATH=<checkout>/src python trial_big_money.py PROD_ROOT SCRATCH_ROOT CONFIG > trial.log 2>&1 &
"""

import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from quant_system.data_platform.sessions import shanghai_now
from quant_system.monitor.big_money import BigMoneyMonitor, MonitorConfig, NotATradingDay, alert_text, prepare

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
prod, scratch, config = Path(sys.argv[1]), Path(sys.argv[2]), MonitorConfig.load(Path(sys.argv[3]))


def clock() -> datetime:
    return shanghai_now().replace(tzinfo=None)


start = datetime.combine(clock().date(), datetime.strptime("09:20", "%H:%M").time())
if clock() > start.replace(hour=15, minute=2):
    start += timedelta(days=1)
wait = (start - clock()).total_seconds()
print(f"{clock():%F %T} waiting {wait / 3600:.1f} h until {start:%F %H:%M}", flush=True)
if wait > 0:
    time.sleep(wait)
day = clock().date()
for attempt in range(10):
    try:
        groups, profile = prepare(prod, config, day)
        break
    except NotATradingDay as exc:
        print(exc, flush=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 (the catch-up may be rebuilding the catalog)
        print(f"prepare failed ({exc}); retrying", flush=True)
        time.sleep(60)
else:
    sys.exit(1)
http = requests.Session()
http.headers["User-Agent"] = "Mozilla/5.0"


def get(url, params):
    time.sleep(0.05)
    r = http.get(url, params=params, timeout=8)
    r.raise_for_status()
    return r.content.decode("gbk", errors="replace")


def on_alert(direction, group, row):
    title, body = alert_text(direction, group, row)
    print(f"{clock():%T} ALERT {title}\n    {body}", flush=True)


print(f"{day}: {len(groups)} groups, {sum(len(g.funds) for g in groups)} funds", flush=True)
BigMoneyMonitor(scratch, config, groups, profile, day, get, on_alert, clock).run()
print(f"{clock():%T} session over", flush=True)
