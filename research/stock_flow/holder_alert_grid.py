"""Thresholds for the position manager's “大涨后股东户数大增” alert: returns after a holder-count disclosure by the
count's rise and the stock's run-up before it (question of 2026-10-25).  Reuses the cninfo counts cached by
shareholder_count.py.  The alert's rule is picked from this grid, so its numbers are in-sample.

    cd ~/L1/MinervaQuantSystem && .venv/bin/python ~/L1/research/stock_flow/holder_alert_grid.py data/market.duckdb \
        ~/L1/research/stock_flow/data
"""

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from shareholder_count import counts  # noqa: E402

database, data = sys.argv[1], Path(sys.argv[2]).expanduser()
hc = counts(data)
con = duckdb.connect(database, read_only=True)
notices = con.execute("SELECT symbol, report_date, min(notice_date) AS notice_date FROM fin_income GROUP BY 1, 2").fetchdf()
for col in ("report_date", "notice_date"):
    notices[col] = pd.to_datetime(notices[col])
ev = hc.merge(notices, on=["symbol", "report_date"]).dropna(subset=["change", "notice_date"])
bars = con.execute("SELECT symbol, trade_date, hfq_open AS o, hfq_close AS c, hfq_low AS l, turnover_cny AS amt FROM "
                   "daily_bars_adjusted WHERE trade_date >= DATE '2015-06-01' AND hfq_close > 0").fetchdf()
o, c, low, amt = (bars.pivot(index="trade_date", columns="symbol", values=k) for k in ("o", "c", "l", "amt"))
dates = pd.DatetimeIndex(c.index)
universe = amt.rolling(20, min_periods=15).mean() >= 3e7
r120 = c / c.shift(120) - 1
f60 = c.shift(-59) / o - 1
x60 = f60.sub(f60.where(universe).mean(axis=1), axis=0)
dd60 = low[::-1].rolling(60, min_periods=30).min()[::-1] / o - 1
cols = {s: k for k, s in enumerate(c.columns)}
rows = []
for r in ev.itertuples(index=False):
    i = dates.searchsorted(r.notice_date, side="right")
    j = cols.get(r.symbol)
    if j is None or i >= len(dates) or i == 0 or not universe.iat[i, j]:
        continue
    rows.append({"change": r.change, "r120": r120.iat[i - 1, j], "x60": x60.iat[i, j], "dd60": dd60.iat[i, j],
                 "year": dates[i].year})
t = pd.DataFrame(rows).dropna()
print(f"{len(t):,} disclosures; all: 60-day excess {t['x60'].mean():+.2%}, fell 20%+ within 60 days "
      f"{(t['dd60'] <= -0.2).mean():.0%}")
out = []
for run in (0.3, 0.5, 1.0):
    for rise in (0.1, 0.2, 0.3, 0.5):
        g = t[(t["r120"] >= run) & (t["change"] >= rise)]
        yearly = g.groupby("year")["x60"].mean()
        out.append({"前120日涨幅≥": f"{run:.0%}", "户数增幅≥": f"{rise:.0%}", "次数": len(g), "每年约": len(g) / 10.5,
                    "60日超额": g["x60"].mean(), "跑赢比例": (g["x60"] > 0).mean(),
                    "60日内跌过20%": (g["dd60"] <= -0.2).mean(), "超额为负的年份": f"{(yearly < 0).sum()}/{len(yearly)}"})
print(pd.DataFrame(out).to_string(index=False, formatters={"每年约": "{:.0f}".format, "60日超额": "{:+.2%}".format,
                                                           "跑赢比例": "{:.0%}".format, "60日内跌过20%": "{:.0%}".format}))
