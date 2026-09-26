"""Synthetic research data (market + reference frames) for factor tests."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from bt_fakes import weekdays
from quant_system.backtest.market_data import MarketData
from quant_system.research.data import ResearchData

BOARD_OF = {"6": "SSE_MAIN", "0": "SZSE_MAIN", "3": "CHINEXT"}


def synthetic_frames(seed: int, sessions: int = 320, symbols: int = 36) -> tuple[dict, dict]:
    """(market frames, research frames) with suspensions, late listings,
    ex-rights events, share changes (some announced after they take effect),
    an industry switch and dividends."""
    rng = np.random.default_rng(seed)
    days = weekdays(date(2020, 1, 2), sessions)
    codes = [f"{'603'[k % 3]}{k:05d}" for k in range(symbols)]
    bars, factors, master, shares, industry, dividends = [], [], [], [], [], []
    for k, symbol in enumerate(codes):
        listed = 0 if k % 6 else int(rng.integers(20, 200))  # some list inside the window
        price = float(rng.uniform(3, 60))
        base_volume = float(rng.uniform(1e5, 5e6))
        for i, day in enumerate(days):
            if i < listed or rng.random() < 0.04:
                continue
            change = rng.normal(0.0005, 0.025)
            open_ = round(price * (1 + rng.normal(0, 0.005)), 2)
            close = round(max(price * (1 + change), 0.5), 2)
            high = round(max(open_, close) * (1 + abs(rng.normal(0, 0.01))), 2)
            low = round(min(open_, close) * (1 - abs(rng.normal(0, 0.01))), 2)
            volume = float(round(base_volume * rng.lognormal(0, 0.4)))
            bars.append({"symbol": symbol, "trade_date": day, "open": open_, "high": high, "low": low,
                         "close": close, "volume_shares": volume, "turnover_cny": volume * close})
            price = close
        factors.append({"symbol": symbol, "effective_date": date(1990, 1, 1), "hfq_factor": 1.0})
        if k % 4 == 0:
            factors.append({"symbol": symbol, "effective_date": days[int(rng.integers(50, sessions))],
                            "hfq_factor": 1.25})
        master.append({"symbol": symbol, "board": BOARD_OF[symbol[0]],
                       "exchange": "SSE" if symbol.startswith("6") else "SZSE",
                       "list_date": days[listed] if listed else date(2010, 1, 4), "delist_date": None})
        total = float(rng.uniform(1e8, 2e9))
        shares.append({"symbol": symbol, "change_date": date(2015, 1, 5), "notice_date": date(2014, 12, 30),
                       "total_shares": total, "float_a_shares": total * 0.7, "record_key": f"{symbol}|a"})
        for n in range(2):
            change_day = days[int(rng.integers(30, sessions))]
            notice = change_day + timedelta(days=int(rng.integers(-10, 25)))  # sometimes announced late
            total *= float(rng.uniform(1.0, 1.3))
            shares.append({"symbol": symbol, "change_date": change_day, "notice_date": notice,
                           "total_shares": total, "float_a_shares": total * 0.8, "record_key": f"{symbol}|{n}"})
        switch = days[int(rng.integers(100, sessions))]
        industry.append({"symbol": symbol, "start_date": date(2014, 2, 21), "end_date": switch,
                         "l1_code": f"{(k % 5 + 1) * 110000}", "l1_name": f"行业{k % 5}"})
        industry.append({"symbol": symbol, "start_date": switch, "end_date": None,
                         "l1_code": f"{(k % 4 + 1) * 110000}", "l1_name": f"行业{k % 4}"})
        for year in (2020, 2021):
            ex = days[min(int(rng.integers(0, sessions)), sessions - 1)]
            dividends.append({"symbol": symbol, "report_date": date(year - 1, 12, 31), "ex_date": ex,
                              "record_date": ex - timedelta(days=1), "cash_per_10": float(rng.uniform(0, 5)),
                              "total_shares": total})
    market = {
        "calendar": pd.DataFrame({"trade_date": days}),
        "bars": pd.DataFrame(bars),
        "factors": pd.DataFrame(factors),
        "master": pd.DataFrame(master),
        "risk": pd.DataFrame([{"symbol": codes[1], "status": "ST", "start_date": days[150], "end_date": days[250],
                               "method": "szse_name_change", "source": "szse_name_change"}]),
        "indices": pd.DataFrame({"symbol": "sh000300", "trade_date": days, "close": 4000.0}),
    }
    research = {"shares": pd.DataFrame(shares), "industry": pd.DataFrame(industry),
                "dividends": pd.DataFrame(dividends)}
    return market, research


def truncate(market: dict, research: dict, cut: date) -> tuple[dict, dict]:
    """What was known at the close of ``cut`` (the calendar is public)."""
    m = dict(market)
    m["bars"] = market["bars"][market["bars"]["trade_date"] <= cut]
    m["factors"] = market["factors"][market["factors"]["effective_date"] <= cut]
    risk = market["risk"][market["risk"]["start_date"] <= cut].copy()
    risk["end_date"] = risk["end_date"].where(risk["end_date"] <= cut, None)
    m["risk"] = risk
    r = dict(research)
    shares = research["shares"]
    r["shares"] = shares[(shares["change_date"] <= cut) & (shares["notice_date"] <= cut)]
    industry = research["industry"][research["industry"]["start_date"] <= cut].copy()
    industry["end_date"] = industry["end_date"].map(lambda d: d if d is not None and d <= cut else None)
    r["industry"] = industry
    r["dividends"] = research["dividends"][research["dividends"]["ex_date"] <= cut]
    return m, r


def build(market: dict, research: dict) -> ResearchData:
    return ResearchData.from_frames(MarketData.from_frames(market), research)
