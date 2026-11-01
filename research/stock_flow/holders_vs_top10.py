"""How often do the shareholder count and the top-ten stake fall together, and why?  Quarter-on-quarter
changes of consecutive quarter ends, 2021-2026, with the total share count and 减持 announcements in between."""

import duckdb
import numpy as np
import pandas as pd

con = duckdb.connect("data/market.duckdb", read_only=True)
q = con.execute("""
    SELECT symbol, end_date, holders, top10_pct, top10_free_pct FROM shareholder_counts
    WHERE end_date >= DATE '2020-12-31' AND holders > 0 AND top10_pct IS NOT NULL
      AND strftime(end_date, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    ORDER BY symbol, end_date""").fetchdf()
q["end_date"] = pd.to_datetime(q["end_date"])
q["prev_end"] = q.groupby("symbol")["end_date"].shift(1)
for col in ("holders", "top10_pct", "top10_free_pct"):
    q[f"prev_{col}"] = q.groupby("symbol")[col].shift(1)
q = q[(q["end_date"] - q["prev_end"]).dt.days.between(80, 100)].copy()
q["d_holders"] = q["holders"] / q["prev_holders"] - 1
q["d_top10"] = q["top10_pct"] - q["prev_top10_pct"]
q["d_free10"] = q["top10_free_pct"] - q["prev_top10_free_pct"]

shares = con.execute("SELECT symbol, change_date, total_shares FROM share_capital WHERE total_shares > 0 "
                     "ORDER BY symbol, change_date").fetchdf()
shares["change_date"] = pd.to_datetime(shares["change_date"])


def shares_at(frame: pd.DataFrame, col: str) -> pd.Series:
    left = frame[["symbol", col]].reset_index().sort_values(col)
    out = pd.merge_asof(left, shares.sort_values("change_date"), left_on=col, right_on="change_date", by="symbol")
    return out.set_index("index")["total_shares"].reindex(frame.index)


q["sh_now"], q["sh_prev"] = shares_at(q, "end_date"), shares_at(q, "prev_end")
q["d_shares"] = q["sh_now"] / q["sh_prev"] - 1
cuts = con.execute("SELECT symbol, notice_date FROM holder_changes WHERE direction = '减持'").fetchdf()
cuts["notice_date"] = pd.to_datetime(cuts["notice_date"])
m = q[["symbol", "prev_end", "end_date"]].reset_index().merge(cuts, on="symbol")
m = m[(m["notice_date"] > m["prev_end"]) & (m["notice_date"] <= m["end_date"])]
q["reduced"] = q.index.isin(m["index"].unique())

print(f"{len(q):,} 个股票季度（2021Q1–2026Q3）")
both = q[(q["d_holders"] < 0) & (q["d_top10"] < 0)]
print("\n户数和前十大合计的变化方向（占全部股票季度）：")
tab = pd.crosstab(np.where(q["d_holders"] < 0, "户数减少", "户数增加"),
                  np.where(q["d_top10"] < 0, "前十大下降", np.where(q["d_top10"] > 0, "前十大上升", "前十大不变")),
                  normalize="all")
print((tab * 100).round(1).to_string())
down = q[q["d_holders"] < 0]
print(f"\n户数减少的季度里，前十大合计下降的占 {(down['d_top10'] < 0).mean():.0%}；"
      f"户数增加的季度里占 {(q[q['d_holders'] > 0]['d_top10'] < 0).mean():.0%}")
print(f"相关系数（户数变化 vs 前十大变化）：{q['d_holders'].corr(q['d_top10'], method='spearman'):+.3f}")
print("\n前十大合计每季变化的幅度（百分点）：", q["d_top10"].describe(percentiles=[.1, .25, .5, .75, .9]).round(2).to_dict())

print("\n“户数减少且前十大下降”的季度里：")
print(f"  总股本增加（增发、可转债转股、股权激励等）：{(both['d_shares'] > 0.001).mean():.0%}"
      f"（全部季度 {(q['d_shares'] > 0.001).mean():.0%}）")
print(f"  当季有大股东减持公告：{both['reduced'].mean():.0%}（全部季度 {q['reduced'].mean():.0%}）")
print(f"  前十大只降了不到 1 个百分点：{(both['d_top10'] > -1).mean():.0%}")
print(f"  十大流通股东合计同时下降：{(both['d_free10'] < 0).mean():.0%}")
explained = (both["d_shares"] > 0.001) | both["reduced"]
print(f"  有股本增加或减持其一：{explained.mean():.0%}")
# shares out of the top ten but not to small holders: the average holding of the rest
rest = (100 - both["top10_pct"]) / 100 * both["sh_now"] / both["holders"]
rest_prev = (100 - both["prev_top10_pct"]) / 100 * both["sh_prev"] / both["prev_holders"]
print(f"  前十大以外的户均持股增加：{(rest / rest_prev > 1).mean():.0%}（中位数 {np.nanmedian(rest / rest_prev - 1):+.1%}）")
