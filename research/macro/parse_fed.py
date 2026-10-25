"""Parse the Fed's federal funds target change tables (federalreserve.gov openmarket pages) into a CSV.

    curl -A Mozilla/5.0 -o openmarket.htm https://www.federalreserve.gov/monetarypolicy/openmarket.htm
    curl -A Mozilla/5.0 -o openmarket_archive.htm https://www.federalreserve.gov/monetarypolicy/openmarket_archive.htm
    python -I parse_fed.py openmarket.htm openmarket_archive.htm fed_target.csv

Download the pages into an empty directory of their own.  FRED and the jin10 series (AKShare
macro_bank_usa_interest_rate, empty from 2025-09 on) were not usable on 2026-10-25.

Each table is preceded by a heading with its year; dates in the tables are effective dates (the day after the
FOMC decision).  Levels before 2008-12 are single targets, after that ranges ("0-0.25"); the upper bound is kept.
"""
import re
import sys
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup

rows = []
for path in sys.argv[1:-1]:
    soup = BeautifulSoup(open(path, encoding="utf-8", errors="replace").read(), "html.parser")
    for table in soup.find_all("table"):
        heading = table.find_previous(lambda tag: tag.name in ("h4", "h3", "h5", "strong", "p", "caption")
                                      and re.fullmatch(r"\s*(19|20)\d\d\s*", tag.get_text()))
        cap = table.find("caption")
        year_text = cap.get_text() if cap and re.search(r"(19|20)\d\d", cap.get_text()) else (
            heading.get_text() if heading else "")
        year = re.search(r"(19|20)\d\d", year_text)
        if not year:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 4:
                continue
            date = datetime.strptime(f"{re.sub(r"[^A-Za-z0-9 ]", "", cells[0]).strip()} {year.group(0)}", "%B %d %Y").date()
            inc = 0 if cells[1] in ("...", "") else max(map(float, re.findall(r"\d+", cells[1])))
            dec = 0 if cells[2] in ("...", "") else max(map(float, re.findall(r"\d+", cells[2])))  # 2008-12: "75-100"
            level = float(cells[3].replace("–", "-").split("-")[-1])
            rows.append({"date": date, "change_bp": inc - dec, "level": level, "level_text": cells[3]})
out = pd.DataFrame(rows).drop_duplicates("date").sort_values("date")
out.to_csv(sys.argv[-1], index=False)
print(len(out), out["date"].min(), out["date"].max())
print(out.tail(12).to_string(index=False))
