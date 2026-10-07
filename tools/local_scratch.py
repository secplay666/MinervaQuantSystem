"""A throwaway local root for UI checks: a copy of a market catalog, the configs, a demo user, an env file.

    .venv/Scripts/python tools/local_scratch.py SCRATCH_DIR [--catalog data/market.duckdb] [--sw-parquet sw_l1.parquet]

Then serve it (``quant-app --root SCRATCH_DIR --env-file SCRATCH_DIR/app.env serve --port 8000 --no-tls``), run
the PC frontend (``pnpm exec vite`` in web/apps/web-antd, which proxies /api to port 8000), and take screenshots
with tools/shots.py.  ``--sw-parquet``: SW industry indices as AKShare returns them (research/money_map/), loaded
as the sw_index_bars table when the catalog has none.  Never point it at a real data directory.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
from pathlib import Path

import duckdb
import pandas as pd
from sqlalchemy import select

from quant_system.app.db import open_database
from quant_system.app.db.models import User, UserRole
from quant_system.app.rbac import sync_roles
from quant_system.app.security import hash_password

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("--catalog", default=str(REPO / "data" / "market.duckdb"))
    parser.add_argument("--sw-parquet")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    (root / "data" / "app").mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "configs", root / "configs", dirs_exist_ok=True)
    catalog = root / "data" / "market.duckdb"
    shutil.copyfile(args.catalog, catalog)
    if args.sw_parquet:
        raw = pd.read_parquet(args.sw_parquet)
        bars = pd.DataFrame({  # noqa: F841 (read by DuckDB below)
            "symbol": raw["代码"].astype(str), "name": raw.get("industry", raw["代码"]),
            "trade_date": pd.to_datetime(raw["日期"]).dt.date, "open": raw["开盘"], "high": raw["最高"],
            "low": raw["最低"], "close": raw["收盘"], "volume_shares": (raw["成交量"] * 1e8).round(),
            "turnover_cny": raw["成交额"] * 1e8})
        with duckdb.connect(str(catalog)) as con:
            con.execute("CREATE OR REPLACE TABLE sw_index_bars AS SELECT * FROM bars ORDER BY symbol, trade_date")
    env = root / "app.env"
    if not env.exists():
        env.write_text(f"MINERVA_SECRET_KEY={secrets.token_urlsafe(48)}\nMINERVA_ENV=test\n", encoding="utf-8")
    _, sessions = open_database(root / "data" / "app" / "app.sqlite")
    username = os.environ.get("SHOTS_USER", "demo")
    with sessions() as session:
        sync_roles(session)
        if session.scalar(select(User).where(User.username == username)) is None:
            user = User(username=username, display_name="演示", must_change_password=False,
                        password_hash=hash_password(os.environ.get("SHOTS_PASSWORD", "Demo-pass-2026")))
            session.add(user)
            session.flush()
            session.add(UserRole(user_id=user.id, role_code="admin"))
            session.commit()
    print(f"scratch root ready: {root} (user {username})")


if __name__ == "__main__":
    main()
