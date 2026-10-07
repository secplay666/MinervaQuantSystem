"""Probe the Eastmoney buyback table (RPTA_WEB_GETHGLIST_NEW): all fields, progress codes, purpose texts, history depth."""

import collections
import json
import re

import pandas as pd

from quant_system.data_platform.providers.eastmoney_dc import collect_pages, dc_page

frame = pd.DataFrame(collect_pages(dc_page, "RPTA_WEB_GETHGLIST_NEW", "", "UPDATEDATE", workers=4))
frame.to_parquet("buybacks_raw.parquet")
print(len(frame), "rows; columns:", list(frame.columns))
for column in ("REPURPROGRESS", "SHARETYPE"):
    print(column, frame[column].value_counts(dropna=False).head(12).to_dict())
notice = pd.to_datetime(frame["NOTICEDATE"], errors="coerce")
print("notice years:", notice.dt.year.value_counts().sort_index().to_dict())
text = frame["REPUROBJECTIVE"].fillna("") + " " + frame.get("REMARK", pd.Series("", index=frame.index)).fillna("")
for word in ("注销", "减少注册资本", "股权激励", "员工持股", "市值管理", "维护公司价值", "可转债"):
    print(f"purpose mentions {word}: {text.str.contains(word).mean():.0%}")
sample = frame.sort_values("UPDATEDATE").tail(3)
print(json.dumps(sample.to_dict(orient="records"), ensure_ascii=False, default=str)[:3000])
