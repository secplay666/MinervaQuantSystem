"""First look (2026-10-04): CSI 300 ETFs — secondary-market activity against primary-market creation.

Run on the server in ~/L1/research/intraday after fetch_intraday.py (Eastmoney parts fetched elsewhere
are copied into ./data).  Prints tables; stores nothing.

Primary market: net creation on day D = (shares_D - shares_{D-1}) x close_D, as the exchanges publish
the next morning.  Secondary market:
* 1-minute bars (Sina, ~8 sessions): amount signed by the minute's move (a tick rule: bars carry no
  side), and "surge minutes" with an amount over 5x the day's median minute;
* 3-second trades with their active side (Eastmoney, the last session only): big prints;
* Eastmoney's daily net inflow by order size, where it could be fetched.
"""

from __future__ import annotations

import glob
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
YI = 1e8


def load(prefix: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA / f"{prefix}_*.parquet")))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()


def shares_with_last_day(etfs: pd.DataFrame) -> pd.DataFrame:
    """The scratch history (to 09-29) plus 09-30 asked from the exchanges directly."""
    shares = pd.read_parquet(DATA / "shares.parquet")[["trade_date", "symbol", "shares"]]
    shares["trade_date"] = pd.to_datetime(shares["trade_date"]).dt.date
    extra = []
    try:
        sys.path.insert(0, str(Path.home() / "L1" / "minerva-dev" / "src"))
        from quant_system.data_platform.providers.akshare_provider import _sse_etf_scale

        sse = _sse_etf_scale("20260930")
        sse = sse[sse["SEC_CODE"].isin(etfs["symbol"])]
        extra.append(pd.DataFrame({"trade_date": date(2026, 9, 30), "symbol": sse["SEC_CODE"],
                                   "shares": sse["TOT_VOL"].astype(float) * 1e4}))
        import akshare as ak

        szse = ak.fund_scale_daily_szse(start_date="20260930", end_date="20260930", symbol="ETF")
        code = next(c for c in szse.columns if "代码" in c)
        units = next(c for c in szse.columns if "份额" in c)
        szse = szse[szse[code].astype(str).isin(etfs["symbol"])]
        extra.append(pd.DataFrame({"trade_date": date(2026, 9, 30), "symbol": szse[code].astype(str),
                                   "shares": szse[units].astype(float)}))
    except Exception as exc:  # noqa: BLE001
        print("09-30 shares not available:", exc)
    return pd.concat([shares, *extra], ignore_index=True).drop_duplicates(["trade_date", "symbol"], keep="last")


def creation(shares: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    frame = shares.sort_values(["symbol", "trade_date"]).copy()
    frame["delta"] = frame.groupby("symbol")["shares"].diff()
    frame = frame.merge(closes, on=["trade_date", "symbol"], how="left")
    frame["creation"] = frame["delta"] * frame["close"]
    # A share split shows as a jump of many times the usual flow: leave such days out.
    frame.loc[(frame["delta"].abs() > 0.2 * frame["shares"]), "creation"] = np.nan
    return frame[["trade_date", "symbol", "creation"]]


def minute_features(bars: pd.DataFrame) -> pd.DataFrame:
    bars = bars.copy()
    bars["time"] = pd.to_datetime(bars["day"])
    bars["trade_date"] = bars["time"].dt.date
    bars["symbol"] = bars["code"].str[2:]
    bars = bars.sort_values(["symbol", "time"])
    bars["move"] = np.sign(bars.groupby(["symbol", "trade_date"])["close"].diff().fillna(bars["close"] - bars["open"]))
    bars["signed"] = bars["move"] * bars["amount"]
    median = bars.groupby(["symbol", "trade_date"])["amount"].transform("median")
    bars["surge"] = bars["amount"] > 5 * median
    bars["tail"] = bars["time"].dt.strftime("%H:%M") >= "14:30"
    rows = []
    for (symbol, day), g in bars.groupby(["symbol", "trade_date"]):
        if len(g) < 200:  # the first, partial session of the window
            continue
        surge = g[g["surge"]]
        top = surge.nlargest(3, "amount")
        rows.append({"trade_date": day, "symbol": symbol, "amount": g["amount"].sum(), "signed": g["signed"].sum(),
                     "tail_signed": g.loc[g["tail"], "signed"].sum(), "surge_n": len(surge),
                     "surge_signed": surge["signed"].sum(),
                     "surge_at": " ".join(f"{t:%H:%M}{'+' if m > 0 else '-' if m < 0 else '='}"
                                          for t, m in zip(top["time"], top["move"]))})
    return pd.DataFrame(rows)


def part_a(etfs: pd.DataFrame) -> None:
    daily = pd.read_parquet(DATA / "etf_daily.parquet")[["trade_date", "symbol", "close"]]
    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.date
    bars = load("bars_1m")
    last_close = bars.assign(trade_date=pd.to_datetime(bars["day"]).dt.date, symbol=bars["code"].str[2:]) \
        .sort_values("day").groupby(["trade_date", "symbol"])["close"].last().reset_index()
    closes = pd.concat([daily, last_close]).drop_duplicates(["trade_date", "symbol"], keep="last")
    flows = creation(shares_with_last_day(etfs), closes)
    feats = minute_features(bars).merge(flows, on=["trade_date", "symbol"], how="left")
    feats["name"] = feats["symbol"].map(etfs.set_index("symbol")["name"])
    print("\n=== A. per session, all eight ETFs together (亿元) ===")
    total = feats.groupby("trade_date")[["creation", "amount", "signed", "tail_signed", "surge_signed"]].sum(min_count=1) / YI
    index = load_index()
    total = total.join(index)
    print(total.round(2).to_string())
    print("\n=== A. 510300 per session (亿元; surge_at = the three biggest surge minutes, + up / - down) ===")
    one = feats[feats["symbol"] == "510300"].set_index("trade_date")
    print((one[["creation", "amount", "signed", "tail_signed", "surge_signed"]] / YI).round(2)
          .join(one[["surge_n", "surge_at"]]).to_string())
    pairs = feats.dropna(subset=["creation"])
    if len(pairs) >= 8:
        for col in ("signed", "tail_signed", "surge_signed"):
            rho = pairs[["creation", col]].corr(method="spearman").iloc[0, 1]
            agree = (np.sign(pairs["creation"]) == np.sign(pairs[col]))[pairs["creation"].abs() > 0].mean()
            print(f"creation vs {col:13s}: Spearman {rho:+.2f}, same sign {agree:.0%} ({len(pairs)} ETF-days)")


def load_index() -> pd.DataFrame:
    index = pd.read_parquet(DATA / "index_1m.parquet")
    index["time"] = pd.to_datetime(index["day"])
    closes = index.groupby(index["time"].dt.date)["close"].last()
    return pd.DataFrame({"csi300_%": (closes.pct_change() * 100).round(2)})


def part_b(etfs: pd.DataFrame) -> None:
    trades = load("trades")
    if trades.empty:
        print("\n(no 3-second trades)")
        return
    print("\n=== B. 2026-09-30, 3-second trades (Eastmoney): side codes vs the price move ===")
    trades["amount"] = trades["price"] * trades["lots"] * 100
    trades = trades[trades["time"] >= "09:30:00"].copy()
    trades["move"] = np.sign(trades.groupby("symbol")["price"].diff())
    print(trades.groupby("side")["move"].agg(["mean", "count"]).round(2).to_string())
    for symbol, g in trades.groupby("symbol"):
        cut = g["amount"].quantile(0.99)
        big = g[g["amount"] >= cut]
        buy, sell = big.loc[big["side"] == "2", "amount"].sum(), big.loc[big["side"] == "1", "amount"].sum()
        g = g.assign(bucket=g["time"].str[:4] + "0")
        profile = g[g["amount"] >= cut].assign(net=lambda f: np.where(f["side"] == "2", f["amount"],
                                                                       np.where(f["side"] == "1", -f["amount"], 0)))
        by_half_hour = profile.groupby(profile["time"].str[:5].str[:4])["net"].sum() / 1e4
        print(f"\n{symbol} {etfs.set_index('symbol')['name'].get(symbol, '')}: {len(g)} prints, top 1% >= {cut / 1e4:.0f} 万元; "
              f"big active buy {buy / YI:.2f} 亿, sell {sell / YI:.2f} 亿")
        print("  net big prints by 10 minutes (万元):", " ".join(f"{k}x:{v:+.0f}" for k, v in by_half_hour.items()))
    minutes = load("flow_minutes")
    if not minutes.empty:
        print("\n=== B. Eastmoney cumulative net inflow at 10:30 / 11:30 / 14:00 / 15:00 (亿元) ===")
        for symbol, g in minutes.groupby("symbol"):
            g = g.set_index(g["time"].str[-5:])
            picks = [t for t in ("10:30", "11:30", "14:00", "15:00") if t in g.index]
            print(symbol, " | ".join(f"{t} super {g.at[t, 'super'] / YI:+.2f} large {g.at[t, 'large'] / YI:+.2f}"
                                     for t in picks))


def part_c(etfs: pd.DataFrame) -> None:
    flows = load("flow_days")
    if flows.empty:
        print("\n(no daily fund flow)")
        return
    shares = pd.read_parquet(DATA / "shares.parquet")[["trade_date", "symbol", "shares"]]
    shares["trade_date"] = pd.to_datetime(shares["trade_date"]).dt.date
    closes = flows[["date", "symbol", "close"]].rename(columns={"date": "trade_date"})
    merged = flows.rename(columns={"date": "trade_date"}).merge(creation(shares, closes), on=["trade_date", "symbol"])
    print("\n=== C. daily net inflow by order size vs creation (Eastmoney, 120 sessions) ===")
    for symbol, g in merged.dropna(subset=["creation"]).groupby("symbol"):
        line = [f"{symbol} {len(g)} days"]
        for col in ("super", "large", "main", "small"):
            rho = g[["creation", col]].corr(method="spearman").iloc[0, 1]
            same = (np.sign(g["creation"]) == np.sign(g[col]))[g["creation"].abs() > 0].mean()
            line.append(f"{col}: rho {rho:+.2f} same sign {same:.0%}")
        print("  ".join(line))
        lagged = g.assign(next_creation=g["creation"].shift(-1))
        rho = lagged[["next_creation", "super"]].corr(method="spearman").iloc[0, 1]
        print(f"   super today vs creation the next day: rho {rho:+.2f}")


def main() -> None:
    etfs = pd.read_parquet(DATA / "etfs.parquet")
    part_a(etfs)
    part_b(etfs)
    part_c(etfs)


if __name__ == "__main__":
    main()
