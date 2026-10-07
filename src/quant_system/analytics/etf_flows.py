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
STRONG_Z = 8.0  # an abnormal day this far out and this large is marked strong
STRONG_MIN_SHARE = 0.02
SPREAD_FLOOR_SHARE = 0.0001  # the usual range is taken as at least 0.01% of the group's size

FUND_COLUMNS = ["trade_date", "symbol", "shares", "close", "aum", "share_change", "flow", "event"]
GROUP_COLUMNS = ["trade_date", "aum", "flow", "flow_pct", "funds", "priced_funds", "events", "z", "abnormal",
                 "strong"]


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
    daily["strong"] = (daily["abnormal"].notna() & (daily["z"].abs() >= STRONG_Z)
                       & (daily["flow_pct"].abs() >= STRONG_MIN_SHARE))
    return daily[GROUP_COLUMNS]


def window_sums(daily: pd.DataFrame, windows: tuple[int, ...] = (1, 5, 20, 60)) -> dict[str, float | None]:
    """Flow over the last N sessions of a group's daily series."""
    flows = daily["flow"].to_numpy()
    return {f"flow_{n}d": (float(np.nansum(flows[-n:])) if len(flows) else None) for n in windows}


# -- money waves (资金波段) ----------------------------------------------------------------------
# A wave: the group's net creation over WAVE_SESSIONS sessions, as a share of its assets before them,
# beyond the WAVE_TAIL / 1 - WAVE_TAIL percentiles of its own history up to the day before (point in
# time).  One event per wave: a new one needs WAVE_GAP sessions without the same extreme.

WAVE_SESSIONS = 10
WAVE_TAIL = 0.02
WAVE_MIN_HISTORY = 250
WAVE_GAP = 20
WAVE_HORIZONS = (5, 20, 60)
WAVE_COLUMNS = ["trade_date", "wave_flow", "wave_pct", "low", "high", "rank", "streak", "index_window", "wave",
                "counter"] + [f"after_{h}" for h in WAVE_HORIZONS]


def flow_waves(daily: pd.DataFrame, index_close: pd.Series) -> pd.DataFrame:
    """Per session: the 10-session flow (CNY) and its share of assets, the
    point-in-time band (``low``/``high``) and percentile (``rank``) of that
    share, the run of same-sign daily flows (``streak``: + creation, -
    redemption), the index change over the window, the wave starting that day
    ('creation' / 'redemption' / None), whether it went against the index
    (``counter``), and the index change over the next 5 / 20 / 60 sessions.

    ``daily``: a group's complete days (trade_date, flow, aum);
    ``index_close``: the group's index by trade_date."""
    frame = daily[["trade_date", "flow", "aum"]].reset_index(drop=True).copy()
    if frame.empty:
        return pd.DataFrame(columns=WAVE_COLUMNS)
    flow = frame["flow"].fillna(0.0)
    frame["wave_flow"] = flow.rolling(WAVE_SESSIONS).sum()
    frame["wave_pct"] = frame["wave_flow"] / frame["aum"].shift(WAVE_SESSIONS)
    past = frame["wave_pct"].shift().expanding(WAVE_MIN_HISTORY)
    frame["low"], frame["high"] = past.quantile(WAVE_TAIL), past.quantile(1 - WAVE_TAIL)
    ranks = frame["wave_pct"].expanding(WAVE_MIN_HISTORY).rank(pct=True)
    frame["rank"] = ranks
    sign = np.sign(flow.to_numpy())
    streak = np.zeros(len(sign))
    for i, s in enumerate(sign):
        streak[i] = 0 if s == 0 else (streak[i - 1] + s if i and np.sign(streak[i - 1]) == s else s)
    frame["streak"] = streak.astype(int)
    close = index_close.reindex(frame["trade_date"]).ffill().to_numpy(dtype=float)
    close = pd.Series(close)
    frame["index_window"] = close / close.shift(WAVE_SESSIONS) - 1
    for h in WAVE_HORIZONS:
        frame[f"after_{h}"] = close.shift(-h) / close - 1
    above = (frame["wave_pct"] > frame["high"]).to_numpy()
    below = (frame["wave_pct"] < frame["low"]).to_numpy()
    wave = [None] * len(frame)
    last = {"creation": -10**9, "redemption": -10**9}
    for i in range(len(frame)):
        for kind, hit in (("creation", above[i]), ("redemption", below[i])):
            if hit:
                if i - last[kind] > WAVE_GAP:
                    wave[i] = kind
                last[kind] = i
    frame["wave"] = wave
    rising = frame["index_window"] > 0
    frame["counter"] = ((frame["wave"] == "redemption") & rising) | ((frame["wave"] == "creation") & ~rising
                                                                     & frame["index_window"].notna())
    return frame[WAVE_COLUMNS]


def wave_base(waves: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    """The index change over each horizon from any day (what a wave is compared with)."""
    out = {}
    for h in WAVE_HORIZONS:
        values = waves[f"after_{h}"].dropna()
        out[str(h)] = {"mean": float(values.mean()) if len(values) else None,
                       "up": float((values > 0).mean()) if len(values) else None, "n": int(len(values))}
    return out
