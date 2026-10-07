"""Time and peak memory of the 市场宽度 page's computation (app/breadth.py) on a catalog, with a DuckDB memory cap.

    PYTHONPATH=<src> python measure_breadth.py <market.duckdb> [memory limit, e.g. 1GB; "" for none]

Run once per setting: the peak (ru_maxrss) is for the whole process.  Linux only.
"""

import resource
import sys
import time
from pathlib import Path

from quant_system.app import breadth

breadth.MEMORY_LIMIT = sys.argv[2] if len(sys.argv) > 2 else breadth.MEMORY_LIMIT
start = time.perf_counter()
overview = breadth.MarketBreadth(Path(sys.argv[1])).overview()
seconds = time.perf_counter() - start
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"limit={breadth.MEMORY_LIMIT or 'none'} {seconds:.1f}s peak {peak:.0f} MB; {len(overview['series'])} days "
      f"from {overview['series'][0]['trade_date']}, latest {overview['latest']['trade_date']}: "
      f"above60 {overview['latest']['above60']:.3f}, {overview['latest']['stocks']} stocks")
