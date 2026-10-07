"""Does creation/redemption of CSI 300 ETFs lead the index?  (research, 2026-10-04)

Daily net creation of every CSI 300 ETF (shares change x close, share splits left out), as a share of
the group's assets the day before, against the CSI 300's return on the same day and over the next
1 / 5 / 20 sessions.  Uses the ETF history of a data root ($MINERVA_DATA, the dev env's by default).
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

SCRATCH = Path(os.environ.get("MINERVA_DATA", Path.home() / "L1" / "minerva-dev" / "data")) / "canonical"
pd.set_option("display.width", 200)


def read(dataset: str) -> pd.DataFrame:
    return pd.concat(pd.read_parquet(f) for f in glob.glob(str(SCRATCH / dataset / "**" / "*.parquet"), recursive=True))


def daily_flows() -> pd.DataFrame:
    master = read("etf_master")
    symbols = set(master.loc[master["index_code"].isin(["000300", "399300"]), "symbol"])
    shares = read("etf_shares")
    shares = shares[shares["symbol"].isin(symbols)][["trade_date", "symbol", "shares"]]
    bars = read("etf_bars")[["trade_date", "symbol", "close"]]
    frame = shares.merge(bars, on=["trade_date", "symbol"], how="inner").sort_values(["symbol", "trade_date"])
    frame[["shares", "close"]] = frame[["shares", "close"]].astype("float64")
    frame["delta"] = frame.groupby("symbol")["shares"].diff()
    frame["aum"] = frame["shares"] * frame["close"]
    split = frame["delta"].abs() > 0.2 * frame["shares"]
    frame["creation"] = np.where(split, np.nan, frame["delta"] * frame["close"])
    day = frame.groupby("trade_date").agg(creation=("creation", "sum"), aum=("aum", "sum"))
    day["flow"] = day["creation"] / day["aum"].shift(1)  # share of the group's assets
    return day


def main() -> None:
    day = daily_flows()
    index = read("index_bars")
    index = index[index["symbol"] == "sh000300"].drop_duplicates("trade_date").set_index("trade_date")["close"].sort_index()
    data = day.join(index.rename("index"), how="inner")
    data.index = pd.to_datetime(data.index)
    data = data[data.index >= "2016-10-10"]  # both exchanges from here
    r = data["index"].pct_change()
    data["r0"] = r
    for h in (1, 5, 20):
        data[f"r{h}"] = data["index"].shift(-h) / data["index"] - 1
    data["flow5"] = data["flow"].rolling(5).sum()
    print(f"{data.index[0]:%Y-%m-%d} .. {data.index[-1]:%Y-%m-%d}, {len(data)} sessions; "
          f"group assets now {data['aum'].iloc[-1] / 1e8:.0f} 亿")

    print("\n== Spearman correlation of the flow with the CSI 300 return ==")
    for name in ("flow", "flow5"):
        cells = [f"{c}: {data[[name, c]].corr(method='spearman').iloc[0, 1]:+.2f}" for c in ("r0", "r1", "r5", "r20")]
        print(f"{name:6s} " + "  ".join(cells))

    print("\n== CSI 300 return by decile of the day's flow (1 = biggest redemption, 10 = biggest creation) ==")
    data["decile"] = pd.qcut(data["flow"].rank(method="first"), 10, labels=range(1, 11))
    table = data.groupby("decile", observed=True).agg(
        flow_bp=("flow", lambda x: x.mean() * 1e4), same_day=("r0", "mean"), next_day=("r1", "mean"),
        next_5=("r5", "mean"), next_20=("r20", "mean"), up_20=("r20", lambda x: (x > 0).mean()))
    for col in ("same_day", "next_day", "next_5", "next_20"):
        table[col] = (table[col] * 100).round(2)
    print(table.round(2).to_string())

    print("\n== the same, by 5-day flow (persistent buying or selling) ==")
    data["decile5"] = pd.qcut(data["flow5"].rank(method="first"), 10, labels=range(1, 11))
    table = data.dropna(subset=["flow5"]).groupby("decile5", observed=True).agg(
        flow5_bp=("flow5", lambda x: x.mean() * 1e4), past_5=("r0", lambda x: np.nan),
        next_5=("r5", "mean"), next_20=("r20", "mean"), up_20=("r20", lambda x: (x > 0).mean()))
    past5 = data["index"] / data["index"].shift(5) - 1
    table["past_5"] = (past5.groupby(data["decile5"], observed=True).mean() * 100).round(2)
    for col in ("next_5", "next_20"):
        table[col] = (table[col] * 100).round(2)
    print(table.round(2).to_string())

    print("\n== does the flow add to what the index itself did? (biggest redemptions: up days vs down days) ==")
    big_out = data[data["decile"] == 1]
    big_in = data[data["decile"] == 10]
    for name, part in (("biggest redemptions", big_out), ("biggest creations", big_in)):
        for label, cond in (("index up that day", part["r0"] > 0), ("index down that day", part["r0"] <= 0)):
            p = part[cond]
            print(f"{name:20s} {label:20s} {len(p):4d} days  next 5 {p['r5'].mean() * 100:+.2f}%  "
                  f"next 20 {p['r20'].mean() * 100:+.2f}%")

    for start, end in (("2026-01-05", "2026-02-27"), ("2026-05-25", "2026-07-17")):
        part = data.loc[start:end]
        print(f"\n== {start} .. {end}: daily net creation (亿) and the CSI 300 ==")
        out = pd.DataFrame({"creation": (part["creation"] / 1e8).round(1), "cum": (part["creation"].cumsum() / 1e8).round(0),
                            "csi300": part["index"].round(0), "ret%": (part["r0"] * 100).round(2)})
        print(out.to_string())


if __name__ == "__main__":
    main()
