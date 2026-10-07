"""Time the market-breadth query on a catalog: share of stocks above their 20/60/120/250-session averages per day."""

import sys
import time

import duckdb

SQL = """
WITH bars AS (
  SELECT symbol, trade_date, hfq_close AS c,
         AVG(hfq_close) OVER w20 AS m20, AVG(hfq_close) OVER w60 AS m60,
         AVG(hfq_close) OVER w120 AS m120, AVG(hfq_close) OVER w250 AS m250,
         COUNT(*) OVER w250 AS n
  FROM daily_bars_adjusted
  WHERE hfq_close > 0
  WINDOW w20 AS (PARTITION BY symbol ORDER BY trade_date ROWS 19 PRECEDING),
         w60 AS (PARTITION BY symbol ORDER BY trade_date ROWS 59 PRECEDING),
         w120 AS (PARTITION BY symbol ORDER BY trade_date ROWS 119 PRECEDING),
         w250 AS (PARTITION BY symbol ORDER BY trade_date ROWS 249 PRECEDING)
)
SELECT trade_date, COUNT(*) AS stocks,
       AVG(CASE WHEN c > m20 THEN 1.0 ELSE 0.0 END) AS above20,
       AVG(CASE WHEN c > m60 THEN 1.0 ELSE 0.0 END) AS above60,
       AVG(CASE WHEN c > m120 THEN 1.0 ELSE 0.0 END) AS above120,
       AVG(CASE WHEN c > m250 THEN 1.0 ELSE 0.0 END) AS above250
FROM bars WHERE n >= 250 GROUP BY 1 ORDER BY 1
"""

con = duckdb.connect(sys.argv[1], read_only=True)
con.execute("SET threads=4")
started = time.time()
frame = con.execute(SQL).fetchdf()
print(f"{len(frame)} days in {time.time() - started:.1f}s")
print(frame.tail(5).to_string(index=False))
print(frame[frame["trade_date"].astype(str).isin(["2008-10-31", "2015-06-12", "2018-12-28", "2024-09-23", "2024-10-08"])].to_string(index=False))
