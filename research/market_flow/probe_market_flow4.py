"""Fourth probe (2026-10-06): do vendors agree on the same day's net inflow, and what ETF data we already hold."""

from __future__ import annotations

import json
import re
import time
import warnings

import duckdb
import pandas as pd
import requests

warnings.filterwarnings("ignore")
SINA = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/"}
DATA = "/home/van/L1/minerva-dev/data/canonical"


def yuan(text) -> float:
    match = re.fullmatch(r"\s*(-?[\d.]+)\s*(亿|万)?\s*", str(text))
    if not match:
        return float("nan")
    return float(match.group(1)) * {"亿": 1e8, "万": 1e4, None: 1.0}[match.group(2)]


def main() -> None:
    import akshare as ak

    pages = []
    for page in range(1, 10):
        response = requests.get(SINA + "MoneyFlow.ssl_bkzj_ssggzj", params=dict(page=page, num=1000, sort="r0_net", asc=0, bankuai="", shichang=""), headers=HEADERS, timeout=30)
        rows = json.loads(response.text) if response.text.strip().startswith("[") else []
        if not rows:
            break
        pages.append(pd.DataFrame(rows))
        time.sleep(0.5)
    sina = pd.concat(pages, ignore_index=True)
    sina["code"] = sina["symbol"].str[-6:]
    for column in ("amount", "netamount", "r0_net", "changeratio"):
        sina[column] = pd.to_numeric(sina[column], errors="coerce")
    print("sina symbol prefixes:", sina["symbol"].str[:2].value_counts().to_dict())

    ths = ak.stock_fund_flow_individual(symbol="即时")
    ths["code"] = ths["股票代码"].astype(str).str.zfill(6)
    ths["ths_net"] = ths["净额"].map(yuan)
    ths["ths_amount"] = ths["成交额"].map(yuan)
    both = sina.merge(ths[["code", "ths_net", "ths_amount"]], on="code")
    both = both[(both["amount"] > 0) & both["ths_net"].notna()]
    print(f"matched {len(both)} stocks; turnover ratio THS/Sina median {(both['ths_amount'] / both['amount']).median():.3f}")
    for column in ("netamount", "r0_net"):
        same = (both[column] > 0) == (both["ths_net"] > 0)
        print(f"  THS net vs Sina {column}: Spearman {both[column].corr(both['ths_net'], method='spearman'):+.2f}, same sign {same.mean():.0%}")
    big = both.nlargest(300, "amount")
    same = (big["r0_net"] > 0) == (big["ths_net"] > 0)
    print(f"  top 300 by turnover, THS net vs Sina main: same sign {same.mean():.0%}")
    print(f"  Sina main flow sum {both['r0_net'].sum() / 1e8:.0f} 亿, all-size {both['netamount'].sum() / 1e8:.0f} 亿; THS net sum {both['ths_net'].sum() / 1e8:.0f} 亿")

    con = duckdb.connect()
    for dataset in ("etf_shares", "etf_bars", "etf_top_holders", "industry_sw"):
        try:
            row = con.execute(f"select count(*), count(distinct symbol) from read_parquet('{DATA}/{dataset}/**/*.parquet', union_by_name=true)").fetchone()
            print(f"{dataset}: rows {row[0]}, symbols {row[1]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{dataset}: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        print(con.execute(f"describe select * from read_parquet('{DATA}/etf_shares/**/*.parquet', union_by_name=true)").fetchdf()[["column_name", "column_type"]].to_string(index=False))
        print(con.execute(f"select min(trade_date), max(trade_date) from read_parquet('{DATA}/etf_shares/**/*.parquet', union_by_name=true)").fetchone())
    except Exception as exc:  # noqa: BLE001
        print(f"etf_shares describe: {exc}")


if __name__ == "__main__":
    main()
