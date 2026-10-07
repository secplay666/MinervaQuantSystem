"""What the buyback progress codes mean, and the units of the holder-change fields (samples from the raw tables)."""

import pandas as pd

raw = pd.read_parquet("buybacks_raw.parquet")
for code, group in raw.groupby("REPURPROGRESS"):
    print(f"== {code}: {len(group)} plans; finish date set {group['FINISHDATE'].notna().mean():.0%}, "
          f"done amount > 0 {(pd.to_numeric(group['REPURAMOUNT'], errors='coerce') > 0).mean():.0%}")
    for row in group.sort_values("UPDATEDATE").tail(2).itertuples(index=False):
        print("   ", row.DIM_SCODE, str(row.DIM_DATE)[:10], str(row.NOTICEDATE)[:10], str(row.FINISHDATE)[:10],
              row.REPURAMOUNT, "|", str(row.REMARK).replace("\r\n", " ")[:90], "|", str(row.BZ).replace("\r\n", " ")[:60])
holders = pd.read_parquet("holder_changes_raw.parquet")
print(holders.columns.tolist())
sample = holders.sort_values("NOTICE_DATE").tail(3)
print(sample[["SECURITY_CODE", "HOLDER_NAME", "DIRECTION", "CHANGE_NUM", "CHANGE_RATE", "CHANGE_FREE_RATIO", "AFTER_HOLDER_NUM",
              "HOLD_RATIO", "FREE_SHARES_RATIO", "AFTER_CHANGE_RATE", "TRADE_AVERAGE_PRICE", "MARKET", "START_DATE",
              "END_DATE", "NOTICE_DATE"]].to_string())
print(holders["MARKET"].value_counts().head(10).to_dict())
