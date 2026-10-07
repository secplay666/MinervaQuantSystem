"""Probe market-wide fund flow sources (2026-10-06): what works from here and how much history."""

from __future__ import annotations

import time
import warnings

import pandas as pd

warnings.filterwarnings("ignore")


def show(name: str, fetch) -> None:
    started = time.time()
    try:
        frame = fetch()
        if frame is None or len(frame) == 0:
            print(f"{name:44s} EMPTY")
            return
        cols = [str(c) for c in frame.columns]
        date_col = next((c for c in frame.columns if any(k in str(c) for k in ("日期", "date", "时间"))), None)
        span = f"{frame[date_col].iloc[0]} .. {frame[date_col].iloc[-1]}" if date_col is not None else ""
        print(f"{name:44s} OK rows={len(frame):5d} {span} ({time.time() - started:.1f}s) cols={cols[:8]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{name:44s} FAIL {type(exc).__name__}: {str(exc)[:90]} ({time.time() - started:.1f}s)")


def main() -> None:
    import akshare as ak

    show("em industry flow rank (today)", lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
    show("em concept flow rank (5 days)", lambda: ak.stock_sector_fund_flow_rank(indicator="5日", sector_type="概念资金流"))
    show("em industry flow history (半导体)", lambda: ak.stock_sector_fund_flow_hist(symbol="半导体"))
    show("em market flow history", lambda: ak.stock_market_fund_flow())
    show("em all stocks flow rank (today)", lambda: ak.stock_individual_fund_flow_rank(indicator="今日"))
    show("ths industry flow (即时)", lambda: ak.stock_fund_flow_industry(symbol="即时"))
    show("ths industry flow (20日排行)", lambda: ak.stock_fund_flow_industry(symbol="20日排行"))
    show("sse margin detail 20260930", lambda: ak.stock_margin_detail_sse(date="20260930"))
    show("szse margin detail 20260930", lambda: ak.stock_margin_detail_szse(date="20260930"))
    show("sse margin detail 20150105", lambda: ak.stock_margin_detail_sse(date="20150105"))
    show("hsgt flow summary (northbound)", lambda: ak.stock_hsgt_fund_flow_summary_em())
    show("hsgt northbound history", lambda: ak.stock_hsgt_hist_em(symbol="北向资金"))


if __name__ == "__main__":
    main()
