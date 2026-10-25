"""盘中大资金: the monitor's files (data/monitor/big_money/<day>/, written by monitor/big_money.py) for the page."""

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
