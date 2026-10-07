"""Second probe (2026-10-06): the sources the first probe lost, Sina money flow, margin, and what EM's market flow measures."""

from __future__ import annotations

import json
import time
import warnings

import pandas as pd
import requests

warnings.filterwarnings("ignore")
SINA = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/"}


def show(name: str, fetch) -> pd.DataFrame | None:
    started = time.time()
    try:
        frame = fetch()
        if frame is None or len(frame) == 0:
            print(f"{name:40s} EMPTY", flush=True)
            return None
        date_col = next((c for c in frame.columns if any(k in str(c) for k in ("日期", "date", "时间", "opendate"))), None)
        span = f"{frame[date_col].iloc[0]} .. {frame[date_col].iloc[-1]}" if date_col is not None else ""
        print(f"{name:40s} OK rows={len(frame):5d} {span} ({time.time() - started:.1f}s)", flush=True)
        print(f"{'':40s} cols={[str(c) for c in frame.columns][:12]}", flush=True)
        return frame
    except Exception as exc:  # noqa: BLE001
        print(f"{name:40s} FAIL {type(exc).__name__}: {str(exc)[:90]} ({time.time() - started:.1f}s)", flush=True)
        return None


def sina(method: str, **params) -> pd.DataFrame:
    response = requests.get(SINA + method, params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    text = response.text.strip()
    return pd.DataFrame(json.loads(text) if text.startswith("[") else [])


def sina_pages(method: str, pages: int, **params) -> pd.DataFrame:
    frames = []
    for page in range(1, pages + 1):
        frame = sina(method, page=page, **params)
        if frame.empty:
            break
        frames.append(frame)
        time.sleep(0.5)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    import akshare as ak

    show("em concept flow rank (5 days)", lambda: ak.stock_sector_fund_flow_rank(indicator="5日", sector_type="概念资金流"))
    show("ths industry flow (即时)", lambda: ak.stock_fund_flow_industry(symbol="即时"))
    show("ths industry flow (20日排行)", lambda: ak.stock_fund_flow_industry(symbol="20日排行"))
    show("ths concept flow (即时)", lambda: ak.stock_fund_flow_concept(symbol="即时"))
    show("ths individual flow (即时)", lambda: ak.stock_fund_flow_individual(symbol="即时"))
    show("sina sector flow (sina industry)", lambda: sina("MoneyFlow.ssl_bkzj_bk", page=1, num=100, sort="netamount", asc=0, fenlei=0))
    show("sina sector flow (csrc industry)", lambda: sina("MoneyFlow.ssl_bkzj_bk", page=1, num=100, sort="netamount", asc=0, fenlei=2))
    hist = show("sina stock flow history 600519", lambda: sina_pages("MoneyFlow.ssl_qsfx_zjlrqs", 40, num=100, sort="opendate", asc=0, daima="sh600519"))
    if hist is not None:
        print(f"{'':40s} last row: {hist.iloc[-1].to_dict()}", flush=True)
    show("sina sector flow trend (new_blhy)", lambda: sina_pages("MoneyFlow.ssl_bkzj_zjlrqs", 10, num=100, sort="opendate", asc=0, bankuai="0/new_blhy"))
    show("szse margin detail 20260929", lambda: ak.stock_margin_detail_szse(date="20260929"))
    show("szse margin detail 20260930", lambda: ak.stock_margin_detail_szse(date="2026-09-30"))
    show("hsgt northbound history", lambda: ak.stock_hsgt_hist_em(symbol="北向资金"))

    market = show("em market flow history", lambda: ak.stock_market_fund_flow())
    if market is not None:
        market = market.sort_values("日期").reset_index(drop=True)
        main_flow = pd.to_numeric(market["主力净流入-净额"], errors="coerce") / 1e8
        change = pd.to_numeric(market["上证-涨跌幅"], errors="coerce")
        print(f"\n  EM market main net inflow, {len(market)} sessions {market['日期'].iloc[0]} .. {market['日期'].iloc[-1]}")
        print(f"  negative days: {(main_flow < 0).mean():.0%}; mean {main_flow.mean():.0f} 亿/day; total {main_flow.sum():.0f} 亿")
        print(f"  same-day Spearman vs SSE change: {main_flow.corr(change, method='spearman'):+.2f}")
        print(f"  next-day Spearman vs SSE change: {main_flow.corr(change.shift(-1), method='spearman'):+.2f}")
        five = main_flow.rolling(5).sum()
        ahead = change[::-1].rolling(5).sum()[::-1].shift(-1)
        print(f"  5-day flow vs next 5-day change Spearman: {five.corr(ahead, method='spearman'):+.2f}")
        cols = [c for c in market.columns if "净额" in str(c)]
        print("  mean by class (亿/day):", {c.split("-")[0]: round(pd.to_numeric(market[c], errors='coerce').mean() / 1e8, 1) for c in cols})


if __name__ == "__main__":
    main()
