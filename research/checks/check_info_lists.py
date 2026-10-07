"""Real-data check (2026-10-07) of the information lists on the dev env: 回购增持 lists, 市场宽度, and the
公司在买 / 减持 tags on the position manager.  Writes only to a throwaway copy of the business DB.

    cd ~/L1/minerva-dev && .venv/bin/python ~/L1/research/checks/check_info_lists.py
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from quant_system.app.db import open_database
from quant_system.app.db.models import User, UserRole
from quant_system.app.main import create_app
from quant_system.app.rbac import sync_roles
from quant_system.app.security import hash_password
from quant_system.app.settings import AppSettings

ROOT = Path.cwd()
work = Path(tempfile.mkdtemp(prefix="minerva-check-"))
db = work / "app.sqlite"
shutil.copyfile(ROOT / "data" / "app" / "app.sqlite", db)
_, sessions = open_database(db)
password = secrets.token_urlsafe(16)
with sessions() as session:
    sync_roles(session)
    if session.scalar(select(User).where(User.username == "check-bot")) is None:
        user = User(username="check-bot", display_name="检查", password_hash=hash_password(password),
                    must_change_password=False)
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role_code="admin"))
        session.commit()

client = TestClient(create_app(AppSettings(root=ROOT, db_path=db, secret_key=secrets.token_urlsafe(48))))
token = client.post("/api/v1/auth/login", json={"username": "check-bot", "password": password}).json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}


def get(path: str) -> dict:
    start = time.perf_counter()
    response = client.get(f"/api/v1{path}", headers=headers)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    print(f"  GET {path}: {time.perf_counter() - start:.2f}s")
    return response.json()


print("== 回购")
buybacks = get("/company-actions/buybacks?days=90")
print(f"  as_of {buybacks['as_of']}, {buybacks['total']} plans in 90 days")
for row in buybacks["rows"]:
    if row["symbol"] in ("600690", "603351", "605259"):
        print(f"  {row['symbol']} {row['name']} {row['progress_name']} {row['kind_name']}: plan "
              f"{row['plan_pct_lower']:.2f}%–{row['plan_pct_upper']:.2f}% of cap, done {row['done_amount']}, "
              f"avg {row['done_avg_price']}, close {row['close']}")
big = get("/company-actions/buybacks?days=90&kind=cancel&min_pct=1")
print(f"  cancel and plan >= 1% of cap: {big['total']}")

print("== 股东增减持")
for direction in ("增持", "减持"):
    rows = get(f"/company-actions/holders?days=90&direction={direction}")
    print(f"  {direction}: {rows['total']} notices; first {[(r['symbol'], r['name'], r['change_pct_total']) for r in rows['rows'][:2]]}")
ming = [r for r in get("/company-actions/holders?days=30&direction=减持")["rows"] if r["symbol"] == "688699"]
print("  688699 明微电子:", [(r["notice_date"], r["change_shares"], r["change_pct_total"]) for r in ming])

print("== 按行业")
industries = get("/company-actions/industries?days=90")["rows"]
print(f"  {len(industries)} industries; top 3 by buybacks: "
      f"{[(r['industry'], r['buyback_companies'], round(r['buyback_amount'] / 1e8, 1)) for r in industries[:3]]}")

print("== 市场宽度")
breadth = get("/breadth")
latest = breadth["latest"]
print(f"  as_of {breadth['as_of']}, {len(breadth['series'])} days from {breadth['series'][0]['trade_date']}")
print(f"  latest {latest['trade_date']}: {latest['stocks']} stocks, above 20/60/120/250 = "
      + " / ".join(f"{latest[f'above{n}']:.1%}" for n in (20, 60, 120, 250))
      + f"; highs {latest['highs']}, lows {latest['lows']}, ups {latest['ups']}, downs {latest['downs']}")
print(f"  industries {len(breadth['industries'])}, days {breadth['industry_days']}")
get("/breadth")  # cached

print("== 仓位管家标签")
added = client.post("/api/v1/pm/items", headers=headers, json={"text": "300750 600155 000333 600519", "group": "检查"})
assert added.status_code in (200, 201), added.text[:300]
for item in client.get("/api/v1/pm/items", headers=headers).json()["items"]:
    if item["symbol"] in ("300750", "600155", "000333", "600519"):
        company = item.get("company")
        print(f"  {item['symbol']} {item.get('name')}: "
              + ("—" if not company else f"在买 {company['buying']} 减持 {company['reducing']} {company['notes'][:2]}"))

shutil.rmtree(work)
print("ok")
