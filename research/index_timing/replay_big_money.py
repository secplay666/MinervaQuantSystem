"""Replay the big-money monitor (src/quant_system/monitor/big_money.py) over the stored sessions: when would it
have alerted?  Only the CSI 300 group has stored trades (the evening intraday step keeps eight CSI 300 ETFs).

    cd ~/L1/MinervaQuantSystem && PYTHONPATH=<checkout>/src .venv/bin/python replay_big_money.py . configs/monitor/big_money.json
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

from quant_system.monitor.big_money import MonitorConfig, prepare, replay


def main() -> None:
    root, config = Path(sys.argv[1]), MonitorConfig.load(Path(sys.argv[2]))
    parts = sorted((root / "data" / "canonical" / "intraday_trades").glob("trade_date=*"))
    rows = []
    for part in parts:
        day = date.fromisoformat(part.name.split("=", 1)[1])
        groups, profile = prepare(root, config, day)
        csi300 = [g for g in groups if g.id == "csi300"]
        trades = pd.read_parquet(part / "data.parquet")
        frame, alerts = replay(day, csi300, trades, profile, config)
        g = csi300[0]
        def at(t):
            r = frame[frame["time"] == t]
            return r.iloc[0] if len(r) else None
        r14, r15 = at("14:00"), at("15:00")
        rows.append({"day": day, "funds": "/".join(f.symbol for f in g.funds), "usual_yi": g.avg_turnover / 1e8,
                     "ratio_14": r14["ratio"], "bignet_14_yi": r14["big_net"] / 1e8, "ratio_15": r15["ratio"],
                     "bignet_15_yi": r15["big_net"] / 1e8, "bigshare_15": r15["big_share"],
                     "alert": ", ".join(f"{d}@{t}" for _, d, t in alerts) or "-"})
    print(pd.DataFrame(rows).to_string(index=False, formatters={
        "usual_yi": "{:.0f}".format, "ratio_14": "{:.2f}".format, "bignet_14_yi": "{:+.1f}".format,
        "ratio_15": "{:.2f}".format, "bignet_15_yi": "{:+.1f}".format, "bigshare_15": "{:.0%}".format}))
    print("profile at 10:30, 11:30, 14:00, 14:30:", [round(float(profile[t]), 3) for t in ("10:30", "11:30", "14:00", "14:30")])


if __name__ == "__main__":
    main()
