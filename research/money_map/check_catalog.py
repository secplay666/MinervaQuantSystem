"""Check the money map on the dev env's catalog (read-only)."""

import time
from pathlib import Path

import duckdb

from quant_system.app.money_map import CODE_OF, MoneyMapQueries

db = Path("data/market.duckdb")
started = time.time()
queries = MoneyMapQueries(db)
overview = queries.overview(156)
print(f"overview {len(overview['frames'])} frames, as_of {overview['as_of']}, start {overview['start']}, "
      f"{time.time() - started:.2f}s")
last = overview["frames"][-1]
print("crowded now:", [(row["code"], row["c"]) for row in last["rows"] if row["zone"] == "crowded"])
state = queries.crowding("801710")
print("801710:", {k: v for k, v in state.items() if k != "events"}, state["events"][-2:])
with duckdb.connect(str(db), read_only=True) as con:
    names = {row[0] for row in con.execute("SELECT DISTINCT l1_name FROM industry_sw WHERE end_date IS NULL").fetchall()}
print("industry_sw names without an index:", sorted(n for n in names if n not in CODE_OF))
