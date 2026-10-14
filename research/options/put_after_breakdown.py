"""Buying at-the-money puts after the index breaks down: did it pay on SSE ETF options?  Question of 2026-10-10.

    cd ~/L1/minerva-dev && PYTHONPATH=src .venv/bin/python ~/L1/research/options/put_after_breakdown.py \
        ~/L1/research/options/data data/market.duckdb

Rules, fixed before looking at results:
  underlyings  510050 (signals on the SSE 50 index sh000016) and 510300 (CSI 300, sh000300)
  signals      at a session's close, on the index: close crosses below its 200-day average; below its 60-day
               average; a new 60-session closing low; the position manager's four-stage view turns to decline
               (steady preset, index variant).  After an event the same signal is ignored for 20 sessions.
               Baseline: every session.
  contract     standard (unadjusted) puts of the nearest expiry with at least 15 sessions left, the one whose
               delta is closest to -0.5 on the signal day (at the money)
  entry        the close of the next session (the signal is only known after the close)
  exit         expiry (the option's close on its last day, about its intrinsic value), or after 10 or 20
               sessions if earlier
  return       exit / entry - 1 on the premium; net: 1% cost on each side
Expiry = the fourth Wednesday of the month, or the next session (exchange rule).  Also reported: the put's
implied volatility at the signal and its percentile among the previous 250 sessions' at-the-money values, and
the index move over the holding period.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_system.position.stages import DECLINE, PRESETS, classify

UNDERLYINGS = {"510050": ("sh000016", "50ETF / 上证50"), "510300": ("sh000300", "300ETF / 沪深300")}
MIN_LEFT = 15
COOLDOWN = 20
HOLDS = {"到期": None, "10 日": 10, "20 日": 20}
COST = 0.01


def fourth_wednesday(month: str) -> date:
    first = date(int(month[:4]), int(month[5:7]), 1)
    offset = (2 - first.weekday()) % 7  # Wednesday = 2
    return first + timedelta(days=offset + 21)


def load(data: Path):
    contracts = pd.read_parquet(data / "contracts_sse.parquet")
    contracts["SECURITY_ID"] = contracts["SECURITY_ID"].astype(str)
    risk = pd.read_parquet(data / "risk_sse.parquet",
                           columns=["TRADE_DATE", "SECURITY_ID", "DELTA_VALUE", "IMPLC_VOLATLTY"])
    risk["SECURITY_ID"] = risk["SECURITY_ID"].astype(str)
    risk["date"] = pd.to_datetime(risk["TRADE_DATE"])
    sina = pd.read_parquet(data / "sina_daily.parquet", columns=["SECURITY_ID", "日期", "收盘", "成交量"])
    sina["SECURITY_ID"] = sina["SECURITY_ID"].astype(str)
    sina["date"] = pd.to_datetime(sina["日期"])
    sina = sina.rename(columns={"收盘": "close", "成交量": "volume"})
    sina.loc[sina["close"] <= 0, "close"] = np.nan  # February 2015: closes missing at Sina
    return contracts, risk, sina


def index_signals(database: Path, symbol: str, days: pd.DatetimeIndex) -> pd.DataFrame:
    with duckdb.connect(str(database), read_only=True) as con:
        bars = con.execute("SELECT trade_date, close FROM index_bars WHERE symbol = ? ORDER BY 1", [symbol]).fetchdf()
    close = bars.set_index(pd.to_datetime(bars["trade_date"]))["close"].astype(float)
    stage = pd.Series(classify(close.to_numpy(), PRESETS["steady"][1].for_index()), index=close.index)
    out = pd.DataFrame(index=close.index)
    for n in (200, 60):
        below = close < close.rolling(n, min_periods=n).mean()
        out[f"跌破 {n} 日均线"] = below & ~below.shift(1, fill_value=True)
    out["创 60 日新低"] = close < close.rolling(60, min_periods=60).min().shift(1)
    out["四阶段转为左侧"] = (stage == DECLINE) & (stage.shift(1) != DECLINE)
    out["close"] = close
    return out.reindex(days)


def thin(flags: pd.Series) -> pd.Series:
    """Events with a cooldown: a signal within COOLDOWN sessions of the previous event is ignored."""
    keep, last = [], -10**9
    for i, flag in enumerate(flags.fillna(False).to_numpy()):
        if flag and i - last > COOLDOWN:
            keep.append(True)
            last = i
        else:
            keep.append(False)
    return pd.Series(keep, index=flags.index)


def trades(underlying: str, contracts, risk, sina, signals: pd.DataFrame) -> pd.DataFrame:
    standard = ~contracts["adjusted"].astype(bool)  # object dtype: ~ alone would give -1/-2
    puts = contracts[(contracts["underlying"] == underlying) & (contracts["kind"] == "put") & standard]
    own = risk[risk["SECURITY_ID"].isin(contracts.loc[contracts["underlying"] == underlying, "SECURITY_ID"])]
    days = pd.DatetimeIndex(sorted(own["date"].unique()))
    pos = {d: i for i, d in enumerate(days)}
    last_day = days[-1]

    def session_on_or_after(d: date):
        i = days.searchsorted(pd.Timestamp(d))
        return days[i] if i < len(days) else None

    expiry = {m: session_on_or_after(fourth_wednesday(m)) for m in puts["expiry_month"].unique()}
    puts = puts.assign(expiry=puts["expiry_month"].map(expiry)).dropna(subset=["expiry"])
    sina_last = sina.groupby("SECURITY_ID")["date"].max()
    check = puts.join(sina_last.rename("sina_last"), on="SECURITY_ID")
    done = check[check["expiry"] < last_day]
    mismatch = int((done["sina_last"] != done["expiry"]).sum())

    cand = own.merge(puts[["SECURITY_ID", "expiry", "strike"]], on="SECURITY_ID")
    cand = cand[cand["DELTA_VALUE"] < 0]
    cand["left"] = cand["expiry"].map(pos) - cand["date"].map(pos)
    cand = cand[cand["left"] >= MIN_LEFT]
    cand["dist"] = (cand["DELTA_VALUE"] + 0.5).abs()
    first_expiry = cand.groupby("date")["expiry"].transform("min")
    cand = cand[cand["expiry"] == first_expiry]
    pick = cand.sort_values(["date", "dist"]).drop_duplicates("date").set_index("date")  # whole rows

    price = sina.drop_duplicates(["SECURITY_ID", "date"]).set_index(["SECURITY_ID", "date"])["close"]
    atm_iv = pick["IMPLC_VOLATLTY"].where(pick["IMPLC_VOLATLTY"] > 0)
    iv_pct = atm_iv.rolling(250, min_periods=120).apply(
        lambda w: np.mean(w[:-1][~np.isnan(w[:-1])] < w[-1]) if not np.isnan(w[-1]) else np.nan, raw=True)

    rows = []
    for t, row in pick.iterrows():
        i = pos[t]
        if i + 1 >= len(days):
            continue
        entry_day = days[i + 1]
        entry = price.get((row["SECURITY_ID"], entry_day), np.nan)
        if not entry or np.isnan(entry):
            continue
        record = {"signal_day": t, "security_id": row["SECURITY_ID"], "strike": row["strike"],
                  "expiry": row["expiry"], "entry_day": entry_day, "entry": entry,
                  "iv": atm_iv.get(t), "iv_pct": iv_pct.get(t)}
        for label, hold in HOLDS.items():
            exit_day = row["expiry"] if hold is None else min(days[min(i + 1 + hold, len(days) - 1)], row["expiry"])
            if exit_day > last_day or (hold is not None and i + 1 + hold >= len(days)):
                record[f"r_{label}"] = np.nan
                continue
            exit_price = price.get((row["SECURITY_ID"], exit_day), np.nan)
            record[f"r_{label}"] = exit_price / entry - 1 if exit_price == exit_price else np.nan
            record[f"exit_{label}"] = exit_day
        rows.append(record)
    out = pd.DataFrame(rows).set_index("signal_day")
    for name in [c for c in signals.columns if c != "close"]:
        out[name] = thin(signals[name]).reindex(out.index).fillna(False)
    close = signals["close"]
    for label in HOLDS:
        out[f"idx_{label}"] = [close.asof(x) / close.asof(e) - 1 if isinstance(x, pd.Timestamp) else np.nan
                              for e, x in zip(out["entry_day"], out.get(f"exit_{label}", pd.Series(index=out.index)))]
    out.attrs["mismatch"] = mismatch
    out.attrs["expired_contracts"] = len(done)
    return out


def summarize(frame: pd.DataFrame, label: str) -> dict:
    r = frame[f"r_{label}"].dropna()
    net = ((1 + r) * (1 - COST) / (1 + COST) - 1) if len(r) else r
    idx = frame.loc[r.index, f"idx_{label}"]
    return {"次数": len(r), "平均收益": r.mean(), "中位数": r.median(), "赚钱的比例": (r > 0).mean(),
            "亏掉九成以上": (r <= -0.9).mean(), "扣成本后平均": net.mean(), "最好一次": r.max(),
            "期间指数平均": idx.mean(), "隐含波动率": frame.loc[r.index, "iv"].mean(),
            "波动率分位": frame.loc[r.index, "iv_pct"].mean()}


def fmt(table: pd.DataFrame) -> str:
    shown = table.copy()
    for column in shown.columns:
        if column == "次数":
            continue
        shown[column] = shown[column].map(lambda v: "" if pd.isna(v) else f"{v:+.0%}" if column not in
                                          ("赚钱的比例", "亏掉九成以上", "隐含波动率", "波动率分位") else f"{v:.0%}")
    return shown.to_string()


def main() -> None:
    data, database = Path(sys.argv[1]).expanduser(), Path(sys.argv[2])
    contracts, risk, sina = load(data)
    all_trades = []
    for underlying, (index, name) in UNDERLYINGS.items():
        days = pd.DatetimeIndex(sorted(risk.loc[risk["SECURITY_ID"].isin(
            contracts.loc[contracts["underlying"] == underlying, "SECURITY_ID"]), "date"].unique()))
        signals = index_signals(database, index, days)
        frame = trades(underlying, contracts, risk, sina, signals)
        frame["underlying"] = underlying
        all_trades.append(frame)
        print(f"\n######## {name}: {frame.index[0].date()} .. {frame.index[-1].date()}, {len(frame)} 个交易日有可买的平值认沽"
              f"（到期日按规则推算，与新浪最后交易日不一致的已到期合约 {frame.attrs['mismatch']} / "
              f"{frame.attrs['expired_contracts']}）")
        groups = {"每天都买（对照）": frame}
        groups.update({s: frame[frame[s]] for s in signals.columns if s != "close"})
        for label in HOLDS:
            table = pd.DataFrame({g: summarize(f, label) for g, f in groups.items()}).T
            print(f"\n== 持有到{label}" if label == "到期" else f"\n== 持有 {label}")
            print(fmt(table))
        yearly = frame.groupby(frame.index.year)["r_到期"].agg(["count", "mean", lambda x: (x > 0).mean()])
        yearly.columns = ["次数", "平均收益", "赚钱的比例"]
        print("\n== 每天都买、持有到期，按年")
        print(yearly.to_string(formatters={"平均收益": "{:+.0%}".format, "赚钱的比例": "{:.0%}".format}))
        events = frame[frame["跌破 200 日均线"]][["entry", "r_到期", "idx_到期", "iv_pct"]]
        print("\n== 跌破 200 日均线的每一次（持有到期）")
        print(events.to_string(formatters={"r_到期": "{:+.0%}".format, "idx_到期": "{:+.1%}".format,
                                           "iv_pct": "{:.0%}".format}))
    pd.concat(all_trades).to_csv(Path(__file__).with_name("put_after_breakdown_trades.csv"), encoding="utf-8-sig")


if __name__ == "__main__":
    main()
