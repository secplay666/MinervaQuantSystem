"""Event study (2026-10-06): what the CSI 300 did after waves of ETF creation or redemption.

A wave: the 10-session net creation of all CSI 300 ETFs, as a share of their assets, beyond the 2nd or
98th percentile of its history; one event per wave (the first day; the next one 20 sessions later at
the earliest).  For each: the index over the 10 sessions of the wave, then over the next 1/5/20/60
sessions, against the same horizons from every day (the base rate).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from creation_vs_index import daily_flows, read

pd.set_option("display.width", 220)


def main() -> None:
    day = daily_flows()
    index = read("index_bars")
    index = index[index["symbol"] == "sh000300"].drop_duplicates("trade_date").set_index("trade_date")["close"].sort_index()
    data = day.join(index.rename("index"), how="inner")
    data.index = pd.to_datetime(data.index)
    data = data[data.index >= "2016-10-10"]
    data["flow10"] = data["flow"].rolling(10).sum()
    data["during"] = data["index"] / data["index"].shift(10) - 1
    for h in (1, 5, 20, 60):
        data[f"next{h}"] = data["index"].shift(-h) / data["index"] - 1
    path = data["index"].to_numpy()
    data["dd20"] = [np.nanmin(path[k + 1:k + 21]) / path[k] - 1 if k + 1 < len(path) else np.nan for k in range(len(path))]
    low, high = data["flow10"].quantile([0.02, 0.98])
    print(f"{data.index[0]:%Y-%m-%d} .. {data.index[-1]:%Y-%m-%d}; a wave: 10-day flow below {low:.1%} or above {high:.1%} of assets")

    def events(mask: pd.Series) -> pd.DataFrame:
        picked, last = [], -10_000
        positions = np.flatnonzero(mask.to_numpy())
        for p in positions:
            if p - last >= 20:
                picked.append(p)
            last = p if p - last >= 20 else last
        return data.iloc[picked]

    base = {h: data[f"next{h}"].mean() for h in (1, 5, 20, 60)}
    base_up = {h: (data[f"next{h}"] > 0).mean() for h in (1, 5, 20, 60)}
    print("base rate (every day):", "  ".join(f"next {h}: {base[h]:+.2%} (up {base_up[h]:.0%})" for h in (1, 5, 20, 60)))
    for name, mask in (("REDEMPTION waves", data["flow10"] <= low), ("CREATION waves", data["flow10"] >= high)):
        ev = events(mask)
        print(f"\n== {name}: {len(ev)} ==")
        table = pd.DataFrame({
            "flow10": (ev["flow10"] * 100).round(1), "flow10_yi": (ev["creation"].rolling(1).sum() * 0).round(0),
            "index": ev["index"].round(0), "during%": (ev["during"] * 100).round(1),
            **{f"next{h}%": (ev[f"next{h}"] * 100).round(1) for h in (1, 5, 20, 60)},
            "dd20%": (ev["dd20"] * 100).round(1)})
        table = table.drop(columns=["flow10_yi"])
        print(table.to_string())
        print("mean  ", "  ".join(f"next {h}: {ev[f'next{h}'].mean():+.2%} (up {(ev[f'next{h}'] > 0).mean():.0%})"
                                   for h in (1, 5, 20, 60)))
        for label, part in (("index rose during the wave", ev[ev["during"] > 0]),
                            ("index fell during the wave", ev[ev["during"] <= 0])):
            if len(part):
                print(f"  {label:27s} {len(part):2d}: " + "  ".join(
                    f"next {h}: {part[f'next{h}'].mean():+.2%}" for h in (5, 20, 60)))


if __name__ == "__main__":
    main()
