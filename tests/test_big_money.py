"""盘中大资金监测 (monitor/big_money.py, app/monitor.py): parsing, the measures, the alert rule, a polled session with
fake responses, replay, and the API."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_system.app.monitor import BigMoneyMissing, BigMoneyQueries
from quant_system.monitor.big_money import (
    TICK_COLUMNS,
    AlertRule,
    BigMoneyMonitor,
    Fund,
    Group,
    MonitorConfig,
    TickBook,
    evaluate,
    group_snapshot,
    minute_of,
    parse_quotes,
    parse_tick_page,
    replay,
    select_funds,
    session_minutes,
    turnover_profile,
)

DAY = date(2026, 10, 15)
LINEAR = pd.Series(np.arange(1, 241) / 240, index=session_minutes())
GROUP = Group("csi300", "沪深300", "sh000300", (Fund("510300", "sh510300", "300ETF", "csi300", 1.0e9),
                                              Fund("159919", "sz159919", "沪深300ETF", "csi300", 0.2e9)))


def quote_line(code: str, price: float, prev: float, amount_wan: float, stamp: str, premium: float = 0.05) -> str:
    f = [""] * 88
    f[0], f[1], f[2], f[3], f[4] = "1", "x", code[2:], str(price), str(prev)
    f[7], f[8], f[30], f[37], f[77], f[78] = "100", "80", stamp, str(amount_wan), str(premium), str(price / 1.0005)
    return f'v_{code}="' + "~".join(f) + '";'


def ticks(rows: list[tuple[str, float, str]]) -> pd.DataFrame:
    """(time, amount, side) -> a fund's trades."""
    return pd.DataFrame([(i, t, 4.0, int(a / 4), a, s) for i, (t, a, s) in enumerate(rows)], columns=TICK_COLUMNS)


def test_parsing_quotes_and_tick_pages() -> None:
    q = parse_quotes(quote_line("sh510300", 4.178, 4.107, 1542324, "20261015143000") + "\n" +
                     quote_line("sh000300", 4105.3, 4050.0, 0, "20261015143000"))
    assert q["sh510300"]["amount"] == pytest.approx(1.542324e10) and q["sh510300"]["premium"] == pytest.approx(0.0005)
    assert q["sh510300"]["at"] == datetime(2026, 10, 15, 14, 30) and q["sh000300"]["prev_close"] == 4050.0
    page = 'v_detail_data_sh510300=[3,"210/09:40:29/4.061/0.000/409/166109/B|211/09:40:32/4.060/-0.001/50/20300/M"]'
    number, rows = parse_tick_page(page)
    assert number == 3 and rows[0] == (210, "09:40:29", 4.061, 40900, 166109.0, "B") and rows[1][5] == "N"
    assert parse_tick_page("v_detail_data_sh510300=") is None and parse_tick_page("") is None


def test_minutes_follow_the_session() -> None:
    assert [minute_of(t) for t in ("09:25:02", "09:30:59", "11:29:59", "11:30:03", "13:00:01", "14:59:59",
                                   "15:00:02")] == ["09:31", "09:31", "11:30", "11:30", "13:01", "15:00", "15:00"]
    m = session_minutes()
    assert len(m) == 240 and m[119:121] == ["11:30", "13:01"]


def test_tick_book_reads_new_pages_only() -> None:
    served = {"sh510300": [(i, f"09:{30 + i // 20:02d}:{i % 20 * 3:02d}", 4.0, 1, 1000.0, "B") for i in range(100)]}
    calls = []

    def get(code: str, page: int) -> str:
        calls.append(page)
        rows = served[code][page * 70:(page + 1) * 70]
        body = "|".join(f"{r[0]}/{r[1]}/{r[2]}/0/{r[3]}/{r[4]:.0f}/{r[5]}" for r in rows)
        return f'v_detail_data_{code}=[{page},"{body}"]' if rows else f"v_detail_data_{code}="

    book = TickBook("sh510300")
    book.refresh(get)
    assert len(book.frame()) == 100 and calls == [0, 1]
    served["sh510300"] += [(i, "10:00:00", 4.0, 1, 1000.0, "S") for i in range(100, 150)]
    calls.clear()
    book.refresh(get)
    assert len(book.frame()) == 150 and calls == [1, 2]  # the last page again, then the new one


def test_profile_and_fund_selection() -> None:
    minutes = session_minutes()
    bars = pd.DataFrame([{"trade_date": d, "symbol": "510300", "time": t, "amount": 2.0 if t <= "10:00" else 1.0}
                         for d in ("2026-10-13", "2026-10-14") for t in minutes])
    profile = turnover_profile(bars)
    assert profile["15:00"] == pytest.approx(1.0) and profile["10:00"] > 30 / 240
    members = pd.DataFrame([("510300", "SSE", "300ETF", "csi300", 4.7e9), ("510310", "SSE", "HS300", "csi300", 1.5e9),
                            ("159919", "SZSE", "沪深300ETF", "csi300", 1.1e9), ("515390", "SSE", "x", "csi300", 1.9e8),
                            ("159300", "SZSE", "y", "csi300", 0.9e8)],
                           columns=["symbol", "exchange", "name", "group_id", "avg_turnover"])
    groups = select_funds(members, {"groups": [{"id": "csi300", "name": "沪深300", "chart_symbol": "sh000300"}]},
                          MonitorConfig())
    assert [f.code for f in groups[0].funds] == ["sh510300", "sh510310", "sz159919"]  # 90% of the turnover


def day_of_trades(buying: bool) -> dict[str, pd.DataFrame]:
    """A session of small two-way trading (1.2 billion, the group's usual turnover), plus (``buying``) 2.4 billion
    of 10-million buys after 14:30."""
    rows = []
    for m in session_minutes():
        clock = (datetime.strptime(m, "%H:%M") - timedelta(seconds=30)).strftime("%H:%M:%S")
        rows += [(clock, 2e6, "B"), (clock, 2e6, "S"), (clock, 1e6, "B")]
    if buying:
        rows += [(f"14:{31 + i // 10:02d}:{i % 10 * 5:02d}", 1e7, "B") for i in range(240)]
    rows.sort(key=lambda r: r[0])
    return {"510300": ticks(rows), "159919": ticks([("10:00:00", 1e6, "B")])}


def test_the_alert_needs_heavy_turnover_big_prints_and_a_large_net() -> None:
    rule = AlertRule()
    quiet, _ = group_snapshot(DAY, "14:59:59", GROUP, day_of_trades(False), LINEAR, 5e6)
    assert evaluate(quiet, GROUP, rule) is None and quiet["big_net"] == 0 and quiet["ratio"] < 1.1
    busy, funds = group_snapshot(DAY, "14:59:59", GROUP, day_of_trades(True), LINEAR, 5e6)
    assert busy["big_net"] == pytest.approx(2.4e9) and evaluate(busy, GROUP, rule) == "buy"
    assert busy["big_net_30m"] == pytest.approx(2.4e9) and len(funds) == 2 and funds[0]["symbol"] == "510300"
    sold = dict(busy, big_net=-busy["big_net"])
    assert evaluate(sold, GROUP, rule) == "sell"
    assert evaluate(dict(busy, time="09:40"), GROUP, rule) is None  # too early in the session
    trades = pd.concat([f.assign(symbol=s) for s, f in day_of_trades(True).items()], ignore_index=True)
    frame, alerts = replay(DAY, [GROUP], trades, LINEAR, MonitorConfig())
    assert len(frame) == 240 and alerts and alerts[0][:2] == ("csi300", "buy") and alerts[0][2] > "14:30"


class FakeSource:
    """Tencent's quotes and pages for the two funds and the index, at a moving clock."""

    def __init__(self, trades: dict[str, pd.DataFrame], quote_day: str = "20261015") -> None:
        self.trades, self.quote_day, self.now = trades, quote_day, datetime(2026, 10, 15, 9, 30)

    def get(self, url: str, params: dict | None) -> str:
        upto = self.now.strftime("%H:%M:%S")
        if params is None:  # quotes
            out = []
            for code in url.split("=", 1)[1].split(","):
                t = self.trades.get(code[2:])
                amount = 0.0 if t is None else float(t.loc[t["time"] <= upto, "amount"].sum())
                out.append(quote_line(code, 4.0, 3.9, amount / 1e4, self.quote_day + self.now.strftime("%H%M%S")))
            return "\n".join(out)
        t = self.trades[params["c"][2:]]
        t = t[t["time"] <= upto]
        rows = t.iloc[params["p"] * 70:(params["p"] + 1) * 70]
        body = "|".join(f"{r.seq}/{r.time}/{r.price}/0/{r.volume // 100}/{r.amount:.0f}/{r.side}" for r in rows.itertuples())
        return f'v_detail_data_{params["c"]}=[{params["p"]},"{body}"]' if len(rows) else "v_detail_data_x="


def test_a_polled_session_writes_files_and_alerts_once(tmp_path: Path) -> None:
    source = FakeSource(day_of_trades(True))
    alerts = []
    monitor = BigMoneyMonitor(tmp_path, MonitorConfig(), [GROUP], LINEAR, DAY, source.get,
                              lambda d, g, row: alerts.append((d, g.id, row["time"])), clock=lambda: source.now)
    for clock in ("10:00", "14:00", "14:40", "14:50", "15:00"):
        source.now = datetime.strptime(f"2026-10-15 {clock}:30", "%Y-%m-%d %H:%M:%S")
        monitor.poll()
    assert [a[:2] for a in alerts] == [("buy", "csi300")]
    monitor.write({s: b.frame() for s, b in monitor.books.items()}, final=True)
    out = tmp_path / "data" / "monitor" / "big_money" / "2026-10-15"
    assert {p.name for p in out.iterdir()} >= {"snapshots.parquet", "funds.parquet", "ticks.parquet", "status.json"}
    snaps = pd.read_parquet(out / "snapshots.parquet")
    assert list(snaps["time"]) == ["10:01", "14:01", "14:41", "14:51", "15:00"]
    assert snaps["premium"].iloc[-1] == pytest.approx(0.0005)

    page = BigMoneyQueries(tmp_path).day(now=datetime(2026, 10, 15, 15, 5))
    g = page["groups"][0]
    assert page["final"] and not page["live"] and g["alerts"] == [{"direction": "buy", "time": alerts[0][2]}]
    assert len(g["series"]) == 5 and g["latest"]["big_net"] == pytest.approx(2.4e9)
    assert {f["symbol"] for f in g["funds"]} == {"510300", "159919"}


def test_the_last_sessions_trades_are_not_taken_for_today(tmp_path: Path) -> None:
    source = FakeSource(day_of_trades(True), quote_day="20261014")  # before the open the quote is yesterday's
    source.now = datetime(2026, 10, 15, 14, 50)
    monitor = BigMoneyMonitor(tmp_path, MonitorConfig(), [GROUP], LINEAR, DAY, source.get, clock=lambda: source.now)
    row = monitor.poll()[0]
    assert row["amount"] == 0 and all(not b.pages for b in monitor.books.values())


def test_no_monitor_data_is_reported(tmp_path: Path) -> None:
    with pytest.raises(BigMoneyMissing):
        BigMoneyQueries(tmp_path).day()


def test_the_api(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from test_api import add_user, auth, env, login  # noqa: F401

    source = FakeSource(day_of_trades(True))
    monitor = BigMoneyMonitor(tmp_path, MonitorConfig(), [GROUP], LINEAR, DAY, source.get, clock=lambda: source.now)
    source.now = datetime(2026, 10, 15, 14, 55)
    monitor.poll()
    from fastapi.testclient import TestClient

    from quant_system.app.main import create_app
    from quant_system.app.settings import AppSettings
    from test_decision import make_root

    root = make_root(tmp_path / "root")
    app = create_app(AppSettings(root=root, db_path=root / "data" / "app" / "app.sqlite", secret_key="s" * 48))
    client = TestClient(app)
    _, sessions = __import__("quant_system.app.db", fromlist=["open_database"]).open_database(
        root / "data" / "app" / "app.sqlite")
    add_user(sessions, "viewer", ["viewer"])
    headers = auth(login(client, "viewer"))
    assert client.get("/api/v1/monitor/big-money", headers=headers).status_code == 404
    app.state.big_money = BigMoneyQueries(tmp_path)
    body = client.get("/api/v1/monitor/big-money", headers=headers).json()
    assert body["day"] == "2026-10-15" and body["groups"][0]["latest"]["time"] == "14:56"
    assert client.get("/api/v1/monitor/big-money", params={"day": "2026-10-14"}, headers=headers).status_code == 404
