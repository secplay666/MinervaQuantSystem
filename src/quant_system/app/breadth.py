"""市场宽度: how much of the market is in an uptrend, day by day, and by SW L1 industry.

Every stock with a year of bars (250 sessions) counts on each of its sessions:
* above its 20 / 60 / 120 / 250-session moving average (hfq closes);
* at a 250-session high or low;
* up or down on the day.
Information about the market's backdrop, not a signal.  Computed once per catalog version (about 5 s and
2 GB on the full history since 2005), in DuckDB.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .market import records

WINDOWS = (20, 60, 120, 250)
MIN_HISTORY = 250
BENCHMARK = "sh000300"
INDUSTRY_LOOKBACK = 20  # sessions back for the change of each industry's breadth
# The API shares the server with production jobs: DuckDB spills to disk past this instead of growing.
MEMORY_LIMIT = "1GB"

FLAGS_SQL = """
CREATE TEMP TABLE breadth_flags AS
WITH bars AS (
  SELECT symbol, trade_date, hfq_close AS c,
         LAG(hfq_close) OVER w AS prev,
         AVG(hfq_close) OVER w20 AS m20, AVG(hfq_close) OVER w60 AS m60,
         AVG(hfq_close) OVER w120 AS m120, AVG(hfq_close) OVER w250 AS m250,
         MAX(hfq_close) OVER w250 AS hi, MIN(hfq_close) OVER w250 AS lo, COUNT(*) OVER w250 AS n
  FROM daily_bars_adjusted
  WHERE hfq_close > 0
  WINDOW w AS (PARTITION BY symbol ORDER BY trade_date),
         w20 AS (PARTITION BY symbol ORDER BY trade_date ROWS 19 PRECEDING),
         w60 AS (PARTITION BY symbol ORDER BY trade_date ROWS 59 PRECEDING),
         w120 AS (PARTITION BY symbol ORDER BY trade_date ROWS 119 PRECEDING),
         w250 AS (PARTITION BY symbol ORDER BY trade_date ROWS 249 PRECEDING)
)
SELECT symbol, trade_date,
       CAST(c > m20 AS INTEGER) AS a20, CAST(c > m60 AS INTEGER) AS a60,
       CAST(c > m120 AS INTEGER) AS a120, CAST(c > m250 AS INTEGER) AS a250,
       CAST(c >= hi AS INTEGER) AS high, CAST(c <= lo AS INTEGER) AS low,
       CAST(c > prev AS INTEGER) AS up, CAST(c < prev AS INTEGER) AS down
FROM bars WHERE n >= {min_history}
"""

MARKET_SQL = """
SELECT trade_date, COUNT(*) AS stocks, AVG(a20) AS above20, AVG(a60) AS above60, AVG(a120) AS above120,
       AVG(a250) AS above250, SUM(high) AS highs, SUM(low) AS lows, SUM(up) AS ups, SUM(down) AS downs
FROM breadth_flags GROUP BY 1 ORDER BY 1
"""

INDUSTRY_SQL = """
WITH latest AS (SELECT symbol, arg_max(l1_name, start_date) AS industry FROM industry_sw GROUP BY symbol)
SELECT f.trade_date, l.industry, COUNT(*) AS stocks, AVG(a20) AS above20, AVG(a60) AS above60,
       AVG(a120) AS above120, AVG(a250) AS above250, SUM(high) AS highs, SUM(low) AS lows
FROM breadth_flags f JOIN latest l USING (symbol)
WHERE f.trade_date IN (SELECT UNNEST(?)) AND l.industry IS NOT NULL
GROUP BY 1, 2
"""


class BreadthMissing(RuntimeError):
    pass


class MarketBreadth:
    def __init__(self, database: Path) -> None:
        self.database = database
        self._state: tuple[float, dict[str, Any] | BreadthMissing] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.database.is_file():
            raise BreadthMissing("行情库不存在")
        key = self.database.stat().st_mtime
        with self._lock:
            if self._state is None or self._state[0] != key:
                try:
                    self._state = (key, self._compute())
                except BreadthMissing as exc:
                    self._state = (key, exc)
            if isinstance(self._state[1], BreadthMissing):
                raise self._state[1]
            return self._state[1]

    def _compute(self) -> dict[str, Any]:
        try:
            with duckdb.connect(str(self.database), read_only=True) as con:
                con.execute("SET threads=4")
                if MEMORY_LIMIT:
                    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
                con.execute(FLAGS_SQL.format(min_history=MIN_HISTORY))
                market = con.execute(MARKET_SQL).fetchdf()
                if market.empty:
                    raise BreadthMissing("没有足够历史的日线")
                days = list(market["trade_date"])
                picked = [days[-1]] + ([days[-1 - INDUSTRY_LOOKBACK]] if len(days) > INDUSTRY_LOOKBACK else [])
                try:
                    industry = con.execute(INDUSTRY_SQL, [picked]).fetchdf()
                except duckdb.CatalogException:  # no industry classification in this catalog
                    industry = pd.DataFrame(columns=["trade_date", "industry"])
                index = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY 1",
                                    [BENCHMARK]).fetchdf()
        except duckdb.CatalogException as exc:
            raise BreadthMissing(str(exc)) from exc
        for frame in (market, industry, index):
            if len(frame):
                frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        return {"market": market, "industry": industry, "index": index, "days": [pd.Timestamp(d).date() for d in picked]}

    def overview(self) -> dict[str, Any]:
        state = self._load()
        market = state["market"]
        last = market.iloc[-1]
        back = market.iloc[-6] if len(market) > 5 else None
        latest = {"trade_date": last["trade_date"], "stocks": int(last["stocks"]), "highs": int(last["highs"]),
                  "lows": int(last["lows"]), "ups": int(last["ups"]), "downs": int(last["downs"])}
        for n in WINDOWS:
            column = f"above{n}"
            values = market[column].to_numpy(dtype=float)
            latest[column] = float(last[column])
            latest[f"{column}_5d"] = float(last[column] - back[column]) if back is not None else None
            latest[f"{column}_rank"] = float(np.mean(values <= values[-1]))  # its place in the whole history
        rows = []
        industry = state["industry"]
        if len(industry):
            now = industry[industry["trade_date"] == state["days"][0]].set_index("industry")
            before = (industry[industry["trade_date"] == state["days"][1]].set_index("industry")
                      if len(state["days"]) > 1 else pd.DataFrame())
            for name, row in now.iterrows():
                item = {"industry": name, "stocks": int(row["stocks"]), "highs": int(row["highs"]),
                        "lows": int(row["lows"])}
                for n in WINDOWS:
                    item[f"above{n}"] = float(row[f"above{n}"])
                    item[f"above{n}_change"] = (float(row[f"above{n}"] - before.loc[name, f"above{n}"])
                                                if name in before.index else None)
                rows.append(item)
            rows.sort(key=lambda item: -item["above60"])
        return {"as_of": last["trade_date"], "rules": {"windows": list(WINDOWS), "min_history": MIN_HISTORY,
                                                       "industry_lookback": INDUSTRY_LOOKBACK},
                "latest": latest, "industries": rows,
                "industry_days": state["days"],
                "series": records(market.round({f"above{n}": 4 for n in WINDOWS})),
                "index": records(state["index"])}


_SHARED: dict[Path, MarketBreadth] = {}
_SHARED_LOCK = threading.Lock()


def market_breadth_for(database: Path) -> MarketBreadth:
    with _SHARED_LOCK:
        if database not in _SHARED:
            _SHARED[database] = MarketBreadth(database)
        return _SHARED[database]
