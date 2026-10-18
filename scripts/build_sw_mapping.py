"""Derive the SW2014 -> SW2021 level-1 mapping from Shenwan's own history.

Writes ``configs/industry/sw2014_to_sw2021_l1.json``, which is reviewed and
committed; ingestion only reads it (the mapping never changes silently).

    .venv/Scripts/python.exe scripts/build_sw_mapping.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from quant_system.data_platform.corporate import SW_SWITCH_DATE, derive_sw2014_mapping, sw_code_table
from quant_system.data_platform.providers.eastmoney_dc import SW_BASE_URL, SW_FILES, sw_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "configs" / "industry" / "sw2014_to_sw2021_l1.json"))
    parser.add_argument("--history", help="local StockClassifyUse_stock.xls (or the raw history.parquet) instead of "
                                          "downloading")
    parser.add_argument("--codes", help="local SwClassCode_2021.xls (or the raw codes.parquet) instead of downloading")
    args = parser.parse_args()
    history = _read(args.history) if args.history else _download("history")
    codes = _read(args.codes) if args.codes else _download("codes")
    mapping = derive_sw2014_mapping(history, codes)
    names = sw_code_table(codes)["l1_name"]
    payload = {
        "description": "SW2014 industry code -> SW2021 level-1 code: a code that is also a SW2021 code keeps its "
                       "SW2021 class (same_code); otherwise the class that stocks classified on both sides of the "
                       f"{SW_SWITCH_DATE} switch moved into (majority), else the SW2014 level-2 / level-1 majority.",
        "switch_date": SW_SWITCH_DATE.isoformat(),
        "sources": [SW_BASE_URL + name for name in SW_FILES.values()],
        "script": "scripts/build_sw_mapping.py",
        "codes": {row.sw2014_code: {"l1_code": row.l1_code, "l1_name": names.get(row.l1_code),
                                    "method": row.method, "stocks": row.stocks, "share": row.share}
                  for row in mapping.itertuples(index=False)},
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(mapping["method"].value_counts().to_dict())
    weak = mapping[(mapping["method"] == "transition") & (mapping["share"] < 0.8)]
    print(f"{len(mapping)} codes; {len(weak)} transition codes with share < 0.8")
    print(weak.sort_values("share").head(20).to_string())


def _read(path: str) -> pd.DataFrame:
    return pd.read_parquet(path) if path.endswith(".parquet") else pd.read_excel(path, dtype=str)


def _download(kind: str, attempts: int = 4) -> pd.DataFrame:
    for attempt in range(1, attempts + 1):
        try:
            return sw_file(kind)
        except Exception as exc:  # the site is slow at times
            if attempt == attempts:
                raise
            print(f"{kind}: {exc}; retrying")
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    main()
