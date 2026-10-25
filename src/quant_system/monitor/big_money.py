"""盘中大资金监测: during the session, every minute, the broad-index ETFs' quotes and 3-second trades, added up
by index group (configs/etf/broad_groups.json), against the same minute of an average session.

Why (docs/research/index-timing-2026-10.md §6): the day's creations are published only after the close, but when
big money buys ETFs in the secondary market it shows during the session.  On the large creation days of October
2026 the four big CSI 300 ETFs traded 3–5 times their usual turnover, single prints of 5 million yuan or more
made 70–80% of it (usually about 20%), and most of it was active buying after 14:00.  Creations made directly in
the primary market do not show here; and big money buying is background, not a buy signal.

Per group and minute (cumulative from the open):
* ``ratio``      turnover / what the funds usually trade by that minute (their 20-session median daily
                 turnover, robust to a few heavy days, times the average cumulative share of the day traded by then, from the stored
                 1-minute bars of the CSI 300 ETFs)
* ``big_net``    active buys minus active sells of single prints >= ``big_order_yuan``; ``big_share`` the share
                 of such prints in the turnover; ``active_net`` the same over all prints
* ``premium``    price over the indicative NAV (IOPV), turnover-weighted, live only
An alert (once per group, direction and day) when, after ``not_before``, the ratio, the big-order net and the
big-order share all pass the thresholds of configs/monitor/big_money.json.

Sources (both reachable from the server, 2026-10-25): Tencent quotes (qt.gtimg.cn: field 77 premium %, 78 IOPV,
30 time, 37 turnover in 10,000 yuan) and Tencent 3-second trades (stock.gtimg.cn, 70 per page, the latest session
only, no date: a session's trades are accepted only when the quote is of today and the trades add up to the
quote's turnover).  Responses are parsed as text, never evaluated.
"""

from __future__ import annotations

import json
import logging
import re
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOGGER = logging.getLogger("big_money")
QUOTE_URL = "http://qt.gtimg.cn/q="
TICK_URL = "http://stock.gtimg.cn/data/index.php"
TICK_PAGE_SIZE = 70
TICK_COLUMNS = ["seq", "time", "price", "volume", "amount", "side"]
SNAPSHOT_COLUMNS = ["day", "time", "group", "amount", "baseline", "ratio", "active_net", "big_buy", "big_sell",
                    "big_net", "big_share", "big_net_30m", "premium", "index_price", "index_change", "funds"]
FUND_COLUMNS = ["day", "time", "group", "symbol", "amount", "ratio", "big_net", "big_share", "premium", "price"]
TICK_PAGE = re.compile(r'\[(\d+),"(.*)"\]', re.S)
MORNING = (time(9, 30), time(11, 30))
AFTERNOON = (time(13, 0), time(15, 0))


# -- configuration ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AlertRule:
    not_before: str = "09:45"
    min_turnover_ratio: float = 2.0
    min_big_net_share_of_daily: float = 0.15
    min_big_net_yuan: float = 2e8
    min_big_share: float = 0.5


@dataclass(frozen=True)
class MonitorConfig:
    groups_config: str = "configs/etf/broad_groups.json"
    coverage: float = 0.9
    max_per_group: int = 5
    min_avg_turnover_yuan: float = 1e8
    big_order_yuan: float = 5e6
    poll_seconds: int = 60
    baseline_sessions: int = 20
    alert: AlertRule = field(default_factory=AlertRule)

    @classmethod
    def load(cls, path: Path) -> MonitorConfig:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        funds = payload.get("funds", {})
        return cls(groups_config=payload.get("groups_config", cls.groups_config),
                   coverage=float(funds.get("coverage", cls.coverage)),
                   max_per_group=int(funds.get("max_per_group", cls.max_per_group)),
                   min_avg_turnover_yuan=float(funds.get("min_avg_turnover_yuan", cls.min_avg_turnover_yuan)),
                   big_order_yuan=float(payload.get("big_order_yuan", cls.big_order_yuan)),
                   poll_seconds=int(payload.get("poll_seconds", cls.poll_seconds)),
                   baseline_sessions=int(payload.get("baseline_sessions", cls.baseline_sessions)),
                   alert=AlertRule(**payload.get("alert", {})))


@dataclass(frozen=True)
class Fund:
    symbol: str
    code: str          # Tencent code: sh510300 / sz159919
    name: str
    group: str
    avg_turnover: float  # usual turnover, yuan per session (median of the last sessions)


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    index_code: str    # Tencent code of the index (chart_symbol)
    funds: tuple[Fund, ...]

    @property
    def avg_turnover(self) -> float:
        return sum(f.avg_turnover for f in self.funds)


def tencent_code(symbol: str, exchange: str) -> str:
    return ("sh" if exchange.upper() in ("SSE", "SH") or symbol.startswith(("5", "6")) else "sz") + symbol


def select_funds(members: pd.DataFrame, groups_config: dict, config: MonitorConfig) -> list[Group]:
    """The most traded funds of each group until ``coverage`` of the group's turnover, at most ``max_per_group``.
    ``members``: symbol, exchange, name, group_id, avg_turnover."""
    out = []
    for g in groups_config["groups"]:
        m = members[(members["group_id"] == g["id"]) & (members["avg_turnover"] >= config.min_avg_turnover_yuan)]
        m = m.sort_values("avg_turnover", ascending=False)
        if m.empty:
            continue
        share = m["avg_turnover"].cumsum() / m["avg_turnover"].sum()
        keep = m[(share.shift(fill_value=0) < config.coverage)].head(config.max_per_group)
        funds = tuple(Fund(r.symbol, tencent_code(r.symbol, r.exchange), str(r.name), g["id"], float(r.avg_turnover))
                      for r in keep.itertuples(index=False))
        out.append(Group(g["id"], g["name"], g.get("chart_symbol", ""), funds))
    return out


# -- parsing ---------------------------------------------------------------------------------------------------

def parse_quotes(text: str) -> dict[str, dict[str, Any]]:
    """Tencent batch quotes ``v_sh510300="1~name~code~price~..."``: price, previous close, turnover (yuan),
    active buy and sell volume (lots), IOPV and premium (funds), quote time."""
    out: dict[str, dict[str, Any]] = {}
    for chunk in text.split(";"):
        m = re.search(r'v_(\w+)="(.*)"', chunk, re.S)
        if not m:
            continue
        f = m.group(2).split("~")
        if len(f) < 38:
            continue

        def num(i: int) -> float:
            try:
                return float(f[i]) if i < len(f) and f[i] not in ("", "-") else float("nan")
            except ValueError:
                return float("nan")

        stamp = f[30]
        out[m.group(1)] = {"price": num(3), "prev_close": num(4), "amount": num(37) * 1e4, "buy_lots": num(7),
                           "sell_lots": num(8), "premium": num(77) / 100 if len(f) > 77 else float("nan"),
                           "iopv": num(78) if len(f) > 78 else float("nan"),
                           "at": datetime.strptime(stamp, "%Y%m%d%H%M%S") if re.fullmatch(r"\d{14}", stamp) else None}
    return out


def parse_tick_page(text: str) -> tuple[int, list[tuple]] | None:
    """One page of Tencent trades ``[page,"seq/time/price/change/lots/amount/side|..."]``; None when empty."""
    m = TICK_PAGE.search(text or "")
    if not m or not m.group(2):
        return None
    rows = []
    for item in m.group(2).split("|"):
        p = item.split("/")
        if len(p) < 7:
            continue
        try:
            rows.append((int(p[0]), p[1], float(p[2]), int(float(p[4])) * 100, float(p[5]),
                         {"B": "B", "S": "S"}.get(p[6], "N")))
        except ValueError:
            continue
    return int(m.group(1)), rows


class TickBook:
    """A fund's trades of the session, fetched page by page: each refresh re-reads the last page and any new ones."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.reset()

    def reset(self) -> None:
        self.pages: dict[int, list[tuple]] = {}
        self.last_page = 0

    def refresh(self, get: Callable[[str, int], str], max_pages: int = 200) -> int:
        page, fetched = self.last_page, 0
        while fetched < max_pages:
            parsed = parse_tick_page(get(self.code, page))
            fetched += 1
            if parsed is None:
                break
            self.pages[page] = parsed[1]
            self.last_page = page
            if len(parsed[1]) < TICK_PAGE_SIZE:
                break
            page += 1
        return fetched

    def frame(self) -> pd.DataFrame:
        rows = [r for p in sorted(self.pages) for r in self.pages[p]]
        frame = pd.DataFrame(rows, columns=TICK_COLUMNS)
        return frame.drop_duplicates("seq").sort_values("seq", ignore_index=True)


# -- the measures ------------------------------------------------------------------------------------------------

def session_minutes() -> list[str]:
    """The 240 minute labels of a session, 09:31 … 11:30, 13:01 … 15:00 (a minute is labelled by its end)."""
    out = []
    for start, end in (MORNING, AFTERNOON):
        t = datetime.combine(date(2000, 1, 1), start)
        while t.time() < end:
            t += timedelta(minutes=1)
            out.append(t.strftime("%H:%M"))
    return out


def minute_of(clock: str) -> str:
    """The session minute (labelled by its end, like the 1-minute bars) a time HH:MM[:SS] falls in: the auction
    counts to 09:31, the lunch break to 11:30, the closing auction to 15:00."""
    t = datetime.strptime(clock[:5], "%H:%M") + timedelta(minutes=1)
    label = t.strftime("%H:%M")
    if label < "09:31":
        return "09:31"
    if "11:30" < label <= "13:00":
        return "11:30"
    return min(label, "15:00")


def turnover_profile(bars: pd.DataFrame) -> pd.Series:
    """Average cumulative share of a session's turnover traded by each minute (1-minute bars of several funds and
    sessions: trade_date, symbol, time, amount).  Linear in the minutes when there are no bars."""
    minutes = session_minutes()
    if bars is None or bars.empty:
        return pd.Series(np.arange(1, 241) / 240, index=minutes)
    curves = []
    for _, g in bars.groupby(["trade_date", "symbol"]):
        a = g.groupby("time")["amount"].sum().reindex(minutes).fillna(0.0)
        if a.sum() <= 0 or (a > 0).sum() < 200:
            continue  # partial or thin sessions
        curves.append(a.cumsum() / a.sum())
    if not curves:
        return pd.Series(np.arange(1, 241) / 240, index=minutes)
    return pd.concat(curves, axis=1).mean(axis=1)


def fund_measures(ticks: pd.DataFrame, upto: str, big: float) -> dict[str, float]:
    """Cumulative measures of one fund's trades up to ``upto`` (HH:MM:SS)."""
    t = ticks[ticks["time"] <= upto] if not ticks.empty else ticks
    if t.empty:
        return {"amount": 0.0, "active_net": 0.0, "big_buy": 0.0, "big_sell": 0.0, "big_amount": 0.0,
                "big_net_30m": 0.0}
    buy, sell = t["side"] == "B", t["side"] == "S"
    large = t["amount"] >= big
    cut = (datetime.strptime(upto, "%H:%M:%S") - timedelta(minutes=30)).strftime("%H:%M:%S")
    recent = t["time"] > cut
    return {"amount": float(t["amount"].sum()),
            "active_net": float(t.loc[buy, "amount"].sum() - t.loc[sell, "amount"].sum()),
            "big_buy": float(t.loc[large & buy, "amount"].sum()), "big_sell": float(t.loc[large & sell, "amount"].sum()),
            "big_amount": float(t.loc[large, "amount"].sum()),
            "big_net_30m": float(t.loc[large & buy & recent, "amount"].sum() - t.loc[large & sell & recent, "amount"].sum())}


def group_snapshot(day: date, upto: str, group: Group, ticks: dict[str, pd.DataFrame], profile: pd.Series,
                   big: float, quotes: dict[str, dict] | None = None) -> tuple[dict, list[dict]]:
    """The group's row and its funds' rows at ``upto``."""
    minute = minute_of(upto)
    frac = float(profile.get(minute, np.nan))
    rows, totals = [], {"amount": 0.0, "active_net": 0.0, "big_buy": 0.0, "big_sell": 0.0, "big_amount": 0.0,
                        "big_net_30m": 0.0, "baseline": 0.0, "prem_w": 0.0, "prem_amount": 0.0}
    for fund in group.funds:
        m = fund_measures(ticks.get(fund.symbol, pd.DataFrame(columns=TICK_COLUMNS)), upto, big)
        base = fund.avg_turnover * frac
        q = (quotes or {}).get(fund.code, {})
        prem = q.get("premium", float("nan"))
        for k in ("amount", "active_net", "big_buy", "big_sell", "big_amount", "big_net_30m"):
            totals[k] += m[k]
        totals["baseline"] += base
        if prem == prem and m["amount"] > 0:
            totals["prem_w"] += prem * m["amount"]
            totals["prem_amount"] += m["amount"]
        rows.append({"day": day, "time": minute, "group": group.id, "symbol": fund.symbol, "amount": m["amount"],
                     "ratio": m["amount"] / base if base > 0 else np.nan, "big_net": m["big_buy"] - m["big_sell"],
                     "big_share": m["big_amount"] / m["amount"] if m["amount"] else np.nan, "premium": prem,
                     "price": q.get("price", np.nan)})
    iq = (quotes or {}).get(group.index_code, {})
    price, prev = iq.get("price", np.nan), iq.get("prev_close", np.nan)
    row = {"day": day, "time": minute, "group": group.id, "amount": totals["amount"], "baseline": totals["baseline"],
           "ratio": totals["amount"] / totals["baseline"] if totals["baseline"] > 0 else np.nan,
           "active_net": totals["active_net"], "big_buy": totals["big_buy"], "big_sell": totals["big_sell"],
           "big_net": totals["big_buy"] - totals["big_sell"],
           "big_share": totals["big_amount"] / totals["amount"] if totals["amount"] else np.nan,
           "big_net_30m": totals["big_net_30m"],
           "premium": totals["prem_w"] / totals["prem_amount"] if totals["prem_amount"] else np.nan,
           "index_price": price, "index_change": price / prev - 1 if prev and prev == prev else np.nan,
           "funds": len(group.funds)}
    return row, rows


def evaluate(row: dict, group: Group, rule: AlertRule) -> str | None:
    """'buy' / 'sell' when the group's row passes the alert thresholds, else None."""
    if row["time"] < rule.not_before or not (row["ratio"] == row["ratio"]) or row["ratio"] < rule.min_turnover_ratio:
        return None
    if not (row["big_share"] == row["big_share"]) or row["big_share"] < rule.min_big_share:
        return None
    need = max(rule.min_big_net_yuan, rule.min_big_net_share_of_daily * group.avg_turnover)
    if row["big_net"] >= need:
        return "buy"
    if row["big_net"] <= -need:
        return "sell"
    return None


def alert_text(direction: str, group: Group, row: dict) -> tuple[str, str]:
    verb = "大额买入" if direction == "buy" else "大额卖出"
    title = f"盘中大资金：{group.name} ETF {verb}（{row['time']}）"
    body = (f"{group.name}相关 {len(group.funds)} 只 ETF 截至 {row['time']} 成交 {row['amount'] / 1e8:.1f} 亿元，"
            f"是平常同一时刻的 {row['ratio']:.1f} 倍；单笔 500 万元以上的大单占 {row['big_share']:.0%}，"
            f"大单主动净{'买入' if row['big_net'] > 0 else '卖出'} {abs(row['big_net']) / 1e8:.1f} 亿元"
            f"（近 30 分钟 {row['big_net_30m'] / 1e8:+.1f} 亿元）。")
    if row["index_change"] == row["index_change"]:
        body += f"指数此时 {row['index_change']:+.2%}。"
    body += "大资金进场是背景信息，不是买卖信号；当天的申购份额收盘后才公布。"
    return title, body


# -- the session runner ------------------------------------------------------------------------------------------

def in_session(now: datetime) -> bool:
    t = now.time()
    return MORNING[0] <= t <= time(11, 31) or AFTERNOON[0] <= t <= time(15, 1)


class BigMoneyMonitor:
    """Polls during one session and writes ``data/monitor/big_money/<day>/``: snapshots.parquet (groups),
    funds.parquet, ticks.parquet (every fund's trades, for later research) and status.json (the latest state)."""

    def __init__(self, root: Path, config: MonitorConfig, groups: list[Group], profile: pd.Series, day: date,
                 http_get: Callable[[str, dict | None], str],
                 on_alert: Callable[[str, Group, dict], None] | None = None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.root, self.config, self.groups, self.profile, self.day = root, config, groups, profile, day
        self.http_get, self.on_alert, self.clock = http_get, on_alert, clock
        self.books = {f.symbol: TickBook(f.code) for g in groups for f in g.funds}
        self.snapshots: list[dict] = []
        self.fund_rows: list[dict] = []
        self.alerted: dict[tuple[str, str], str] = {}
        self.out = root / "data" / "monitor" / "big_money" / day.isoformat()
        self.errors = 0

    def _ticks_page(self, code: str, page: int) -> str:
        return self.http_get(TICK_URL, {"appn": "detail", "action": "data", "c": code, "p": page})

    def quotes(self) -> dict[str, dict]:
        codes = [f.code for g in self.groups for f in g.funds] + [g.index_code for g in self.groups if g.index_code]
        out: dict[str, dict] = {}
        for i in range(0, len(codes), 60):
            out.update(parse_quotes(self.http_get(QUOTE_URL + ",".join(codes[i:i + 60]), None)))
        return out

    def poll(self) -> list[dict]:
        """One round: quotes, trades, rows, alerts.  Returns the group rows written."""
        now = self.clock()
        quotes = self.quotes()
        ticks: dict[str, pd.DataFrame] = {}
        for g in self.groups:
            for f in g.funds:
                q = quotes.get(f.code, {})
                if q.get("at") is None or q["at"].date() != self.day:
                    continue  # the quote is not of today yet: the trades would be the last session's
                book = self.books[f.symbol]
                try:
                    book.refresh(self._ticks_page)
                except Exception as exc:  # network: skip the fund this round
                    self.errors += 1
                    LOGGER.warning("trades %s: %s", f.code, exc)
                frame = book.frame()
                total, quoted = float(frame["amount"].sum()) if not frame.empty else 0.0, q.get("amount", 0.0)
                if quoted and total > quoted * 1.5 + 5e6:  # the source still serves the last session
                    book.reset()
                    continue
                ticks[f.symbol] = frame
        upto = min(now, datetime.combine(self.day, time(15, 0, 59))).strftime("%H:%M:%S")
        written = []
        for g in self.groups:
            row, funds = group_snapshot(self.day, upto, g, ticks, self.profile, self.config.big_order_yuan, quotes)
            self.snapshots = [s for s in self.snapshots if not (s["group"] == g.id and s["time"] == row["time"])]
            self.fund_rows = [s for s in self.fund_rows if not (s["group"] == g.id and s["time"] == row["time"])]
            self.snapshots.append(row)
            self.fund_rows.extend(funds)
            written.append(row)
            direction = evaluate(row, g, self.config.alert)
            if direction and (g.id, direction) not in self.alerted:
                self.alerted[(g.id, direction)] = row["time"]
                if self.on_alert:
                    self.on_alert(direction, g, row)
        self.write(ticks, final=False)
        return written

    def write(self, ticks: dict[str, pd.DataFrame] | None = None, final: bool = False) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.snapshots, columns=SNAPSHOT_COLUMNS).to_parquet(self.out / "snapshots.parquet", index=False)
        pd.DataFrame(self.fund_rows, columns=FUND_COLUMNS).to_parquet(self.out / "funds.parquet", index=False)
        if ticks is not None and (final or len(self.snapshots) % (10 * max(len(self.groups), 1)) == 0):
            frames = [f.assign(symbol=s) for s, f in ticks.items() if not f.empty]
            if frames:
                pd.concat(frames, ignore_index=True).to_parquet(self.out / "ticks.parquet", index=False)
        status = {"day": self.day.isoformat(), "updated": self.clock().isoformat(timespec="seconds"),
                  "final": final, "big_order_yuan": self.config.big_order_yuan, "errors": self.errors,
                  "alerts": [{"group": g, "direction": d, "time": t} for (g, d), t in self.alerted.items()],
                  "groups": [{"id": g.id, "name": g.name, "index": g.index_code,
                              "avg_turnover": g.avg_turnover,
                              "funds": [{"symbol": f.symbol, "name": f.name, "avg_turnover": f.avg_turnover}
                                        for f in g.funds]} for g in self.groups]}
        tmp = self.out / "status.json.tmp"
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.out / "status.json")

    def run(self, sleep: Callable[[float], None] = time_module.sleep) -> None:
        """Poll every ``poll_seconds`` from the open to just after the close; idle over the lunch break."""
        last_ticks: dict[str, pd.DataFrame] = {}
        while True:
            now = self.clock()
            if now.time() > time(15, 2):
                break
            if in_session(now):
                try:
                    self.poll()
                except Exception as exc:  # one bad round must not end the session
                    self.errors += 1
                    LOGGER.warning("poll failed: %s", exc)
            elapsed = (self.clock() - now).total_seconds()
            sleep(max(5.0, self.config.poll_seconds - elapsed))
        for book_symbol, book in self.books.items():
            last_ticks[book_symbol] = book.frame()
        self.write(last_ticks, final=True)


def replay(day: date, groups: list[Group], trades: pd.DataFrame, profile: pd.Series,
           config: MonitorConfig) -> tuple[pd.DataFrame, list[tuple[str, str, str]]]:
    """The monitor run over a stored session (canonical intraday_trades: symbol, seq, time, price, volume, amount,
    side): its group rows at the end of each minute and the alerts it would have raised (no quotes: no premium or
    index change)."""
    ticks = {s: g[TICK_COLUMNS].reset_index(drop=True) for s, g in trades.groupby("symbol")}
    rows, alerts, seen = [], [], set()
    for minute in session_minutes():
        upto = (datetime.strptime(minute, "%H:%M") - timedelta(seconds=1)).strftime("%H:%M:%S")
        if minute == "15:00":
            upto = "15:00:59"
        for g in groups:
            row, _ = group_snapshot(day, upto, g, ticks, profile, config.big_order_yuan)
            rows.append(row)
            direction = evaluate(row, g, config.alert)
            if direction and (g.id, direction) not in seen:
                seen.add((g.id, direction))
                alerts.append((g.id, direction, row["time"]))
    return pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS), alerts


# -- setting up a session ----------------------------------------------------------------------------------------

class NotATradingDay(RuntimeError):
    pass


def prepare(root: Path, config: MonitorConfig, day: date) -> tuple[list[Group], pd.Series]:
    """The groups with their funds and usual turnover (sessions before ``day``) and the intraday turnover profile
    (the stored 1-minute bars of the funds, last ``baseline_sessions`` complete sessions before ``day``)."""
    import duckdb

    from ..data_platform.etf import group_members, load_etf_groups

    groups_config = load_etf_groups(root / config.groups_config)
    with duckdb.connect(str(root / "data" / "market.duckdb"), read_only=True) as con:
        open_day = con.execute("SELECT count(*) FROM trading_calendar WHERE trade_date = ?", [day]).fetchone()[0]
        if not open_day:
            raise NotATradingDay(f"{day} 不是交易日")
        master = con.execute("SELECT * FROM etf_master").fetchdf()
        turnover = con.execute(
            "SELECT symbol, median(turnover_cny) AS avg_turnover FROM (SELECT symbol, turnover_cny, row_number() OVER "
            "(PARTITION BY symbol ORDER BY trade_date DESC) AS rn FROM etf_bars WHERE trade_date < ?) WHERE rn <= ? "
            "GROUP BY symbol", [day, config.baseline_sessions]).fetchdf()
    members = group_members(master, groups_config).merge(turnover, on="symbol", how="inner")
    groups = select_funds(members, groups_config, config)
    bars_root = root / "data" / "canonical" / "intraday_bars"
    parts = sorted(p for p in bars_root.glob("trade_date=*") if p.name.split("=", 1)[1] < day.isoformat())
    frames = []
    for part in parts[-config.baseline_sessions:]:
        b = pd.read_parquet(part / "data.parquet", columns=["trade_date", "symbol", "time", "amount"])
        frames.append(b[b["symbol"].str.fullmatch(r"\d{6}")])
    profile = turnover_profile(pd.concat(frames, ignore_index=True) if frames else None)
    return groups, profile
