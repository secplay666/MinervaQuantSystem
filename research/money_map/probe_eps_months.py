"""Which months of Eastmoney research reports carry EPS forecasts (page 1 of a few months per year)."""

import time

from probe_expectations import report_list

for year in range(2014, 2027):
    for month in (1, 4, 7, 10):
        begin, end = f"{year}-{month:02d}-01", f"{year}-{month:02d}-28"
        if begin > "2026-10-01":
            break
        payload = report_list(begin, end)
        data = payload.get("data") or []
        eps = sum(1 for row in data if row.get("predictNextYearEps") not in (None, ""))
        print(f"{begin[:7]} hits={payload.get('hits'):>5} page 1 with next-year EPS {eps:>3}/{len(data)}", flush=True)
        time.sleep(0.3)
