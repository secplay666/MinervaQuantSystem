"""Money flowing into broad-index ETFs, from daily shares outstanding.

A fund's net subscription on day t is estimated as

    flow_t = (shares_t - shares_{t-1}) * close_t

(creations and redemptions happen at the day's NAV, which the close tracks
closely for broad-index funds).  Days where the share change is not money
are left out and marked:

* ``split``: shares jump by 1.4x or more (either way) and the price moves
  inversely by about as much on the same or an adjacent day, a share split
  or merge (份额折算);
* ``jump``: the same share jump without prices to verify it;
* ``gap``: more than five sessions since the previous record;
* ``new``: the fund's first record (its initial offering is not a flow).

A group's day is abnormal when the flow is far outside its usual range
(robust z-score against the trailing 250 sessions, from the median and the
interquartile range) and also large against the group's size.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SPLIT_MIN_RATIO = 1.4  # share change that is looked at as a possible split
SPLIT_TOLERANCE = 1.25  # shares x price must come back within this factor
MAX_GAP_SESSIONS = 5
BASELINE_SESSIONS = 250
BASELINE_MIN_SESSIONS = 60
ABNORMAL_Z = 4.0
ABNORMAL_MIN_SHARE = 0.005  # and at least 0.5% of the group's size the day before
SPREAD_FLOOR_SHARE = 0.0001  # the usual range is taken as at least 0.01% of the group's size

FUND_COLUMNS = ["trade_date", "symbol", "shares", "close", "aum", "share_change", "flow", "event"]
GROUP_COLUMNS = ["trade_date", "aum", "flow", "flow_pct", "funds", "priced_funds", "events", "z", "abnormal"]


def fund_flows(shares: pd.DataFrame, closes: pd.DataFrame, sessions: list[date]) -> pd.DataFrame:
    """Per fund and day: shares, close, aum (CNY), share change, flow (CNY), event.

    ``shares``: trade_date, symbol, shares; ``closes``: trade_date, symbol,
    close; ``sessions``: the trading calendar (sorted), for gap lengths.
    """
    if shares.empty:
        return pd.DataFrame(columns=FUND_COLUMNS)
    frame = shares[["trade_date", "symbol", "shares"]].merge(
        closes[["trade_date", "symbol", "close"]], on=["trade_date", "symbol"], how="left")
    frame = frame.sort_values(["symbol", "trade_date"], ignore_index=True)
    frame["shares"] = frame["shares"].astype("float64")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce").astype("float64")
    grouped = frame.groupby("symbol", sort=False)
    # A missing close (a day without trading) falls back to the fund's last known close.
    frame["close"] = grouped["close"].ffill()
    previous = grouped[["shares", "close", "trade_date"]].shift()
    index = np.searchsorted(np.array(sessions, dtype="datetime64[D]"),
                            pd.to_datetime(frame["trade_date"]).to_numpy(dtype="datetime64[D]"))
    previous_index = pd.Series(index).groupby(frame["symbol"]).shift()

    ratio = frame["shares"] / previous["shares"]
    price_ratio = frame["close"] / previous["close"]
    near_price = pd.concat([price_ratio, price_ratio.groupby(frame["symbol"]).shift(1),
                            price_ratio.groupby(frame["symbol"]).shift(-1)], axis=1)
    big = np.abs(np.log(ratio)) >= np.log(SPLIT_MIN_RATIO)
    offsets = np.abs(np.log(near_price.mul(ratio, axis=0))) <= np.log(SPLIT_TOLERANCE)
    # A big change with prices that do not offset it is real money and stays.
    event = pd.Series(np.select(
        [previous["shares"].isna().to_numpy(), ((index - previous_index) > MAX_GAP_SESSIONS).to_numpy(),
         (big & offsets.any(axis=1)).to_numpy(), (big & near_price.isna().all(axis=1)).to_numpy()],
        ["new", "gap", "split", "jump"], default=""), index=frame.index).replace("", None)

    frame["share_change"] = frame["shares"] - previous["shares"]
    frame["aum"] = frame["shares"] * frame["close"]
    frame["flow"] = (frame["share_change"] * frame["close"]).where(event.isna())
    frame["event"] = event
    return frame[FUND_COLUMNS]


def group_flows(funds: pd.DataFrame) -> pd.DataFrame:
    """Sum of fund flows per day (the funds of one group), with the
    robust z-score and the abnormal flag ('in' / 'out' / None)."""
    if funds.empty:
        return pd.DataFrame(columns=GROUP_COLUMNS)
    daily = funds.groupby("trade_date").agg(
        aum=("aum", "sum"), flow=("flow", "sum"), flows=("flow", "count"), funds=("symbol", "nunique"),
        priced_funds=("aum", "count"), events=("event", "count"),
    ).reset_index()
    daily["flow"] = daily["flow"].where(daily["flows"] > 0)  # no usable fund flow that day
    daily["flow_pct"] = daily["flow"] / daily["aum"].shift()
    history = daily["flow"].shift()
    window = history.rolling(BASELINE_SESSIONS, min_periods=BASELINE_MIN_SESSIONS)
    median = window.median()
    # A quiet group (flows nearly the same every day) would divide by almost nothing.
    spread = np.maximum((window.quantile(0.75) - window.quantile(0.25)) / 1.349,
                        SPREAD_FLOOR_SHARE * daily["aum"].shift())
    daily["z"] = ((daily["flow"] - median) / spread.where(spread > 0)).round(2)
    inflow = (daily["z"] >= ABNORMAL_Z) & (daily["flow_pct"] >= ABNORMAL_MIN_SHARE)
    outflow = (daily["z"] <= -ABNORMAL_Z) & (daily["flow_pct"] <= -ABNORMAL_MIN_SHARE)
    daily["abnormal"] = np.select([inflow, outflow], ["in", "out"], default=None)
    daily["abnormal"] = daily["abnormal"].where(daily["abnormal"].notna(), None)
    return daily[GROUP_COLUMNS]


def window_sums(daily: pd.DataFrame, windows: tuple[int, ...] = (1, 5, 20, 60)) -> dict[str, float | None]:
    """Flow over the last N sessions of a group's daily series."""
    flows = daily["flow"].to_numpy()
    return {f"flow_{n}d": (float(np.nansum(flows[-n:])) if len(flows) else None) for n in windows}
