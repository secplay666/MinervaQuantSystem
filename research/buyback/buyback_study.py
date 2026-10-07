"""Do buybacks (and holder increases) lead to gains?  Testing "回购注销 + 比例不低 + 业绩不差 -> 半年胜率 70%" (2026-10-07).

Run on the server in ~/L1/research/buyback with the dev env's code and data:
    PYTHONPATH=~/L1/minerva-dev/src ~/L1/minerva-dev/.venv/bin/python buyback_study.py ~/L1/minerva-dev/data/market.duckdb
Inputs: buybacks_raw.parquet (probe_buybacks.py), holder changes fetched here (holder_changes_raw.parquet).

Point in time: an event counts from the first session after its announcement (bought at that open,
hfq), and is selected only with what was known then: the purpose and the planned amount of the plan, and
the latest annual/quarterly report published before it.  Completion is not used.  Outcome: the next 126
sessions (about half a year): the return, its excess over the equal-weighted market, and whether it was up.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_system.data_platform.providers.eastmoney_dc import dc_page

HOLD = 126
HERE = Path(__file__).resolve().parent


def holder_changes() -> pd.DataFrame:
    """All holder increases and decreases since 2016, a year at a time.  Sorting by the notice date alone
    leaves rows of one day in no fixed order, so pages overlap; a sort on the whole key keeps them apart.
    A year that still comes back a few rows short is kept and reported (research use)."""
    path = HERE / "holder_changes_raw.parquet"
    if not path.exists():
        frames = []
        sort = "NOTICE_DATE,SECURITY_CODE,HOLDER_NAME,START_DATE,END_DATE,CHANGE_NUM"
        for year in range(2016, 2027):
            window = f"(NOTICE_DATE>='{year}-01-01')(NOTICE_DATE<'{year + 1}-01-01')"
            rows, pages, count = dc_page("RPT_SHARE_HOLDER_INCREASE", window, 1, sort, ",".join("1" for _ in sort.split(",")))
            for page in range(2, pages + 1):
                rows += dc_page("RPT_SHARE_HOLDER_INCREASE", window, page, sort,
                                ",".join("1" for _ in sort.split(",")))[0]
            frame = pd.DataFrame(rows).drop_duplicates()
            print(f"holder changes {year}: {len(frame)} of {count} rows", flush=True)
            frames.append(frame)
        pd.concat(frames, ignore_index=True).to_parquet(path)
    return pd.read_parquet(path)


def market(con: duckdb.DuckDBPyConnection) -> tuple[pd.DatetimeIndex, pd.Series]:
    """Sessions and the equal-weighted market's cumulative level (hfq daily returns of every stock)."""
    daily = con.execute("""
        SELECT trade_date, avg(r) AS r FROM (
          SELECT trade_date, hfq_close / lag(hfq_close) OVER (PARTITION BY symbol ORDER BY trade_date) - 1 AS r
          FROM daily_bars_adjusted) WHERE r IS NOT NULL AND abs(r) < 0.5 GROUP BY 1 ORDER BY 1""").fetchdf()
    sessions = pd.DatetimeIndex(pd.to_datetime(daily["trade_date"]))
    return sessions, pd.Series((1 + daily["r"].to_numpy()).cumprod(), index=sessions)


def outcomes(con, events: pd.DataFrame, sessions: pd.DatetimeIndex, level: pd.Series) -> pd.DataFrame:
    """Entry at the open of the first session after ``date``; exit at the close HOLD-1 sessions later."""
    events = events.copy()
    entry_pos = sessions.searchsorted(pd.to_datetime(events["date"]), side="right")
    events["entry"] = [sessions[i] if i < len(sessions) else pd.NaT for i in entry_pos]
    events["exit"] = [sessions[i + HOLD - 1] if i + HOLD - 1 < len(sessions) else pd.NaT for i in entry_pos]
    events = events.dropna(subset=["entry"])
    symbols = sorted(events["symbol"].unique())
    bars = con.execute("SELECT symbol, trade_date, hfq_open, hfq_close, close FROM daily_bars_adjusted "
                       "WHERE list_contains(?, symbol)", [symbols]).fetchdf()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars = bars.set_index(["symbol", "trade_date"]).sort_index()
    rows = []
    for e in events.itertuples(index=False):
        try:
            series = bars.loc[e.symbol]
        except KeyError:
            continue
        after = series[series.index >= e.entry]
        if after.empty or after.index[0] > e.entry + pd.Timedelta(days=10):  # suspended at entry
            continue
        entry_open = after["hfq_open"].iloc[0]
        before = series[series.index < e.entry]
        last_close = before["close"].iloc[-1] if len(before) else np.nan  # raw, for the market cap
        if pd.isna(e.exit):
            ret = excess = np.nan
        else:
            held = after[after.index <= e.exit]
            ret = held["hfq_close"].iloc[-1] / entry_open - 1  # delisted or suspended: its last close
            prev = sessions[sessions.get_loc(e.entry) - 1] if sessions.get_loc(e.entry) else e.entry
            excess = ret - (level[e.exit] / level[prev] - 1)
        rows.append({**e._asdict(), "ret": ret, "excess": excess, "last_close": last_close})
    return pd.DataFrame(rows)


def latest_profit(con, events: pd.DataFrame) -> pd.DataFrame:
    """The latest report published before each event: cumulative parent net profit and its year-on-year change."""
    fin = con.execute("SELECT symbol, report_date, notice_date, parent_net_profit FROM fin_income "
                      "WHERE notice_date IS NOT NULL AND (version = 1 OR version IS NULL)").fetchdf()
    fin["report_date"] = pd.to_datetime(fin["report_date"])
    fin["notice_date"] = pd.to_datetime(fin["notice_date"])
    fin = fin.sort_values("notice_date")
    out = []
    for e in events.itertuples(index=False):
        known = fin[(fin["symbol"] == e.symbol) & (fin["notice_date"] < pd.Timestamp(e.date))]
        if known.empty:
            out.append((np.nan, np.nan))
            continue
        last = known.sort_values(["report_date", "notice_date"]).iloc[-1]
        year_ago = known[known["report_date"] == last["report_date"] - pd.DateOffset(years=1)]
        prior = year_ago["parent_net_profit"].iloc[-1] if len(year_ago) else np.nan
        growth = (last["parent_net_profit"] - prior) / abs(prior) if prior and not pd.isna(prior) else np.nan
        out.append((last["parent_net_profit"], growth))
    events = events.copy()
    events["profit"], events["profit_yoy"] = zip(*out) if out else ([], [])
    return events


def shares_at(con, events: pd.DataFrame) -> pd.Series:
    shares = con.execute("SELECT symbol, change_date, total_shares FROM share_capital WHERE total_shares > 0").fetchdf()
    shares["change_date"] = pd.to_datetime(shares["change_date"])
    result = []
    for e in events.itertuples(index=False):
        known = shares[(shares["symbol"] == e.symbol) & (shares["change_date"] <= pd.Timestamp(e.date))]
        result.append(known.sort_values("change_date")["total_shares"].iloc[-1] if len(known) else np.nan)
    return pd.Series(result, index=events.index)


def stats(frame: pd.DataFrame) -> dict:
    done = frame.dropna(subset=["ret"])
    if done.empty:
        return {"n": 0}
    wins, losses = done.loc[done["ret"] > 0, "ret"], done.loc[done["ret"] <= 0, "ret"]
    return {"n": len(done), "win": (done["ret"] > 0).mean(), "mean": done["ret"].mean(), "median": done["ret"].median(),
            "excess": done["excess"].mean(), "beat_market": (done["excess"] > 0).mean(),
            "payoff": wins.mean() / abs(losses.mean()) if len(wins) and len(losses) and losses.mean() else np.nan}


def show(label: str, frame: pd.DataFrame) -> None:
    s = stats(frame)
    if not s["n"]:
        print(f"  {label:34s} n=0")
        return
    print(f"  {label:34s} n={s['n']:5d}  胜率 {s['win']:5.1%}  平均 {s['mean']:+6.1%}  中位 {s['median']:+6.1%}  "
          f"超额 {s['excess']:+6.1%}  跑赢 {s['beat_market']:5.1%}  盈亏比 {s['payoff']:.2f}")


def main() -> None:
    database = sys.argv[1]
    con = duckdb.connect(database, read_only=True)
    sessions, level = market(con)

    # Base rate: every stock on every 20th session (bought at the next open, held 126 sessions).
    sample_dates = sessions[sessions >= "2016-01-01"][::20][:-7]
    base = con.execute("SELECT DISTINCT symbol FROM daily_bars_adjusted WHERE trade_date >= DATE '2016-01-01'").fetchdf()
    base_events = pd.DataFrame([(s, d) for d in sample_dates for s in base["symbol"].sample(150, random_state=d.day)],
                               columns=["symbol", "date"])
    base_out = outcomes(con, base_events, sessions, level)

    if sys.argv[2:] == ["holders"]:  # the buyback part was already run
        holder_part(con, sessions, level)
        return
    raw = pd.read_parquet(HERE / "buybacks_raw.parquet")
    text = raw["REPUROBJECTIVE"].fillna("")
    plans = pd.DataFrame({
        "symbol": raw["DIM_SCODE"].astype(str), "date": pd.to_datetime(raw["DIM_DATE"]).dt.normalize(),
        "cancel": text.str.contains("注销|减少注册资本"),
        "incentive": text.str.contains("股权激励|员工持股"),
        "upper": pd.to_numeric(raw["REPURAMOUNTLIMIT"], errors="coerce"),
        "lower": pd.to_numeric(raw["REPURAMOUNTLOWER"], errors="coerce"),
    }).dropna(subset=["date"])
    plans = plans[plans["date"] >= "2016-01-01"]
    plans["purpose"] = np.where(plans["cancel"], "注销", np.where(plans["incentive"], "激励/员工持股", "其他"))
    plans = latest_profit(con, plans)
    plans["shares"] = shares_at(con, plans)
    events = outcomes(con, plans, sessions, level)
    events["cap"] = events["last_close"] * events["shares"]
    events["ratio"] = events["lower"].fillna(events["upper"] / 2) / events["cap"]
    events["good"] = (events["profit"] > 0) & (events["profit_yoy"] >= 0)
    events.to_parquet(HERE / "buyback_events.parquet")

    print(f"== 基准：随便哪天买一只股票（2016 年起，每 20 个交易日抽 150 只）")
    show("任意股票", base_out)
    print(f"\n== 回购（首次公告后下一交易日开盘买入，持有 {HOLD} 个交易日）")
    show("全部回购", events)
    for purpose in ("注销", "激励/员工持股", "其他"):
        show(f"用途：{purpose}", events[events["purpose"] == purpose])
    cancel = events[events["purpose"] == "注销"]
    for threshold in (0.005, 0.01, 0.02):
        show(f"注销 且 计划下限 ≥ 市值 {threshold:.1%}", cancel[cancel["ratio"] >= threshold])
    show("注销 且 业绩不差（盈利且同比不降）", cancel[cancel["good"]])
    for threshold in (0.005, 0.01, 0.02):
        show(f"注销 + 业绩不差 + 比例 ≥ {threshold:.1%}", cancel[cancel["good"] & (cancel["ratio"] >= threshold)])
    show("注销 + 业绩差", cancel[~cancel["good"]])
    print("\n  按年份（注销 + 业绩不差 + 比例 ≥ 1%；基准为同年任意股票）:")
    pick = cancel[cancel["good"] & (cancel["ratio"] >= 0.01)]
    for year in range(2018, 2027):
        p, b = pick[pick["entry"].dt.year == year], base_out[base_out["entry"].dt.year == year]
        sp, sb = stats(p), stats(b)
        if sp["n"]:
            print(f"    {year}: n={sp['n']:4d} 胜率 {sp['win']:5.1%} 平均 {sp['mean']:+6.1%} 超额 {sp['excess']:+6.1%}"
                  f"  | 基准胜率 {sb.get('win', float('nan')):5.1%} 平均 {sb.get('mean', float('nan')):+6.1%}")

    holder_part(con, sessions, level)


def holder_part(con, sessions: pd.DatetimeIndex, level: pd.Series) -> None:
    changes = holder_changes()
    changes["date"] = pd.to_datetime(changes["NOTICE_DATE"], errors="coerce").dt.normalize()
    changes = changes[changes["date"] >= "2016-01-01"]
    grouped = (changes.assign(symbol=changes["SECURITY_CODE"].astype(str),
                              rate=pd.to_numeric(changes["CHANGE_RATE"], errors="coerce"))
               .groupby(["symbol", "date", "DIRECTION"], as_index=False)["rate"].sum())
    grouped = outcomes(con, grouped.rename(columns={"DIRECTION": "direction"}), sessions, level)
    grouped.to_parquet(HERE / "holder_events.parquet")
    print("\n== 股东增减持（公告后下一交易日开盘买入，持有 126 个交易日）")
    for direction in ("增持", "减持"):
        part = grouped[grouped["direction"] == direction]
        show(f"{direction}（全部）", part)
        show(f"{direction}（合计 ≥ 总股本 1%）", part[part["rate"] >= 1])


if __name__ == "__main__":
    main()
