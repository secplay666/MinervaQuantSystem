"""盘中大资金 and 恐慌指数: the monitors' files (data/monitor/big_money/<day>/ and data/monitor/volatility/, written
by monitor/big_money.py and monitor/volatility.py) for the pages."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .market import records

SERIES = ["time", "amount", "baseline", "ratio", "active_net", "big_net", "big_share", "big_net_30m", "premium",
          "index_price", "index_change"]


class BigMoneyMissing(LookupError):
    pass


class BigMoneyQueries:
    def __init__(self, root: Path) -> None:
        self.base = root / "data" / "monitor" / "big_money"

    def days(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted((p.name for p in self.base.iterdir() if (p / "status.json").is_file()), reverse=True)

    def day(self, day: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        days = self.days()
        if not days:
            raise BigMoneyMissing("还没有监测数据（交易日 09:30 起每分钟更新）")
        day = day or days[0]
        if day not in days:
            raise BigMoneyMissing(f"{day} 没有监测数据")
        folder = self.base / day
        status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
        snaps = pd.read_parquet(folder / "snapshots.parquet") if (folder / "snapshots.parquet").is_file() else None
        funds = pd.read_parquet(folder / "funds.parquet") if (folder / "funds.parquet").is_file() else None
        updated = datetime.fromisoformat(status["updated"])
        now = now or datetime.now()
        live = not status.get("final") and now - updated < timedelta(minutes=3)
        alerts = {(a["group"], a["direction"]): a["time"] for a in status.get("alerts", [])}
        groups = []
        for g in status["groups"]:
            series = snaps[snaps["group"] == g["id"]].sort_values("time") if snaps is not None else pd.DataFrame()
            latest = series.iloc[-1] if len(series) else None
            fund_rows = []
            if funds is not None and latest is not None:
                f = funds[(funds["group"] == g["id"]) & (funds["time"] == latest["time"])]
                names = {x["symbol"]: x["name"] for x in g["funds"]}
                fund_rows = records(f.assign(name=f["symbol"].map(names))[
                    ["symbol", "name", "amount", "ratio", "big_net", "big_share", "premium", "price"]])
            groups.append({
                "id": g["id"], "name": g["name"], "index": g["index"], "usual_turnover": g["avg_turnover"],
                "funds": fund_rows or [{"symbol": x["symbol"], "name": x["name"]} for x in g["funds"]],
                "latest": records(series.tail(1)[SERIES])[0] if latest is not None else None,
                "series": records(series[SERIES]) if len(series) else [],
                "alerts": [{"direction": d, "time": t} for (gid, d), t in alerts.items() if gid == g["id"]],
            })
        return {"day": day, "days": days[:30], "updated": status["updated"], "final": bool(status.get("final")),
                "live": live, "big_order_yuan": status.get("big_order_yuan"), "errors": status.get("errors", 0),
                "groups": groups}


class VolatilityMissing(LookupError):
    pass


class VolatilityQueries:
    """The intraday minutes of a day (data/monitor/volatility/<day>/) and the daily history (daily.parquet)."""

    HISTORY_YEARS = 3

    def __init__(self, root: Path) -> None:
        self.base = root / "data" / "monitor" / "volatility"
        self._history: tuple[float, pd.DataFrame] | None = None

    def days(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted((p.name for p in self.base.iterdir() if (p / "status.json").is_file()), reverse=True)

    def history(self) -> pd.DataFrame:
        path = self.base / "daily.parquet"
        if not path.is_file():
            return pd.DataFrame(columns=["id", "trade_date", "value"])
        mtime = path.stat().st_mtime
        if self._history is None or self._history[0] != mtime:
            frame = pd.read_parquet(path)
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
            self._history = (mtime, frame)
        return self._history[1]

    def day(self, day: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        days = self.days()
        history = self.history()
        if not days and history.empty:
            raise VolatilityMissing("还没有恐慌指数数据（交易日 09:30 起每分钟更新；历史每天早上回算）")
        if day is not None and day not in days:
            raise VolatilityMissing(f"{day} 没有盘中数据")
        day = day or (days[0] if days else None)
        status: dict[str, Any] = {}
        minutes = pd.DataFrame(columns=["time", "id", "value"])
        if day:
            folder = self.base / day
            status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
            if (folder / "minutes.parquet").is_file():
                minutes = pd.read_parquet(folder / "minutes.parquet")
        now = now or datetime.now()
        live = bool(status) and not status.get("final") and \
            now - datetime.fromisoformat(status["updated"]) < timedelta(minutes=3)
        alerts = {a["id"]: a["time"] for a in status.get("alerts", [])}
        indices = status.get("indices") or [{"id": i} for i in sorted(history["id"].unique())]
        out = []
        for meta in indices:
            own = history[history["id"] == meta["id"]].sort_values("trade_date")
            before = own[own["trade_date"] < pd.Timestamp(day).date()] if day else own
            start = (pd.Timestamp(day) if day else pd.Timestamp.now()) - pd.DateOffset(years=self.HISTORY_YEARS)
            reference = before[before["trade_date"] >= start.date()]["value"]
            series = minutes[minutes["id"] == meta["id"]].sort_values("time")
            series = series[series["value"].notna()]
            latest = series.iloc[-1] if len(series) else None
            value = float(latest["value"]) if latest is not None else (float(own["value"].iloc[-1]) if len(own) else None)
            previous = meta.get("previous_close")
            if previous is None and len(before):
                previous = float(before["value"].iloc[-1])
            out.append({
                "id": meta["id"], "name": meta.get("name", meta["id"]), "exchange": meta.get("exchange"),
                "underlying": meta.get("underlying"), "value": value, "previous_close": previous,
                "change": value / previous - 1 if value is not None and previous else None,
                "percentile": float((reference <= value).mean()) if value is not None and len(reference) else None,
                "latest": records(series.tail(1))[0] if latest is not None else None,
                "series": records(series[["time", "value", "near_vol", "next_vol"]]) if len(series) else [],
                "history": records(own[own["trade_date"] >= (pd.Timestamp.now() - pd.DateOffset(years=self.HISTORY_YEARS)).date()]
                                   [["trade_date", "value"]]),
                "history_from": own["trade_date"].min() if len(own) else None,
                "median": float(own["value"].median()) if len(own) else None,
                "alert": alerts.get(meta["id"]),
            })
        return {"day": day, "days": days[:30], "updated": status.get("updated"), "final": bool(status.get("final")),
                "live": live, "errors": status.get("errors", 0), "history_years": self.HISTORY_YEARS, "indices": out}

