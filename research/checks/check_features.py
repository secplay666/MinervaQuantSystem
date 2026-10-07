"""Real-data check (2026-10-07) of two features on the dev env: manual-account ex-rights confirmation, and the
ETF holders (national team) page.  Writes only to a throwaway copy of the business DB.

    cd ~/L1/minerva-dev && .venv/bin/python ~/L1/research/check_features.py
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from datetime import date
from pathlib import Path

import duckdb
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
    user = User(username="check-bot", display_name="检查", password_hash=hash_password(password),
                must_change_password=False)
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role_code="admin"))
    session.commit()

client = TestClient(create_app(AppSettings(root=ROOT, db_path=db, secret_key=secrets.token_urlsafe(48))))
token = client.post("/api/v1/auth/login", json={"username": "check-bot", "password": password}).json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Stocks with an ex-date between May and September 2026: two with bonus/transfer shares, two cash-only.
with duckdb.connect(str(ROOT / "data" / "market.duckdb"), read_only=True) as con:
    events = con.execute("""
        SELECT symbol, ex_date, cash_per_10, bonus_per_10, transfer_per_10 FROM dividends
        WHERE ex_date BETWEEN DATE '2026-05-06' AND DATE '2026-09-25' AND progress LIKE '%实施%'
        ORDER BY symbol""").fetchdf()
shares = events[(events["bonus_per_10"].fillna(0) + events["transfer_per_10"].fillna(0)) > 0].head(2)
cash_only = events[(events["bonus_per_10"].fillna(0) + events["transfer_per_10"].fillna(0)) == 0].head(2)
picked = list(shares.itertuples(index=False)) + list(cash_only.itertuples(index=False))
print("picked:", [(p.symbol, str(p.ex_date), p.cash_per_10, p.bonus_per_10, p.transfer_per_10) for p in picked])

account = client.post("/api/v1/accounts", headers=headers, json={
    "account_id": "check-manual", "name": "除权检查", "mode": "manual", "cash": "1000000",
    "start_date": "2026-04-30"})
assert account.status_code == 201, account.text
preview = client.post("/api/v1/accounts/check-manual/holdings/preview", headers=headers, json={
    "as_of": "2026-04-30", "cash": "500000",
    "rows": [{"symbol": s, "qty": 1000, "cost_price": 10} for s in sorted({p.symbol for p in picked})]})
assert preview.status_code in (200, 201), preview.text
print("preview errors:", preview.json().get("errors"))
commit = client.post("/api/v1/accounts/check-manual/holdings/commit", headers=headers,
                     json={"batch_id": preview.json()["batch_id"], "reason": "除权检查：录入持仓"})
assert commit.status_code in (200, 201), commit.text

pending = client.get("/api/v1/accounts/check-manual/corporate-actions", headers=headers).json()["rows"]
print(f"\npending corporate actions: {len(pending)}")
for row in pending:
    print("  ", {k: row.get(k) for k in ("symbol", "name", "ex_date", "quantity", "new_quantity", "cash", "cash_fen",
                                         "description", "event_id")})
def num(value) -> float:
    return 0.0 if value is None or value != value else float(value)


for p in picked:
    expected_qty = round(1000 * (1 + (num(p.bonus_per_10) + num(p.transfer_per_10)) / 10))
    expected_cash = 1000 * num(p.cash_per_10) / 10
    print(f"  expected {p.symbol}: quantity {expected_qty}, cash {expected_cash:.2f}")

for row in pending:
    cash = row.get("cash")
    if cash is None and row.get("cash_fen") is not None:
        cash = row["cash_fen"] / 100
    applied = client.post("/api/v1/accounts/check-manual/corporate-actions", headers=headers, json={
        "event_id": row["event_id"], "new_quantity": row.get("new_quantity", row.get("quantity")),
        "cash": str(cash or 0), "reason": "除权检查"})
    print("  apply", row["symbol"], applied.status_code, applied.text[:80])
left = client.get("/api/v1/accounts/check-manual/corporate-actions", headers=headers).json()["rows"]
holdings = client.get("/api/v1/accounts/check-manual", headers=headers).json()
print(f"pending after applying: {len(left)}")
print("holdings:", [(h["symbol"], h["qty"]) for h in holdings.get("holdings", [])], "cash_fen", holdings.get("cash_fen"))

print("\n== ETF holders (national team), CSI 300 group")
holders = client.get("/api/v1/etf/groups/csi300/holders", headers=headers).json()
for period in holders.get("periods", []):
    print(f"  {period['report_date']}: {period['funds']} funds disclosed, national {period['national_value'] / 1e8:.0f} 亿, "
          f"{period['national_share']:.1%} of the group;", {k: round(v / 1e8) for k, v in period['by_class'].items()})
print("  latest period:", holders.get("latest_period"), "; funds listed:", len(holders.get("funds", [])))
shutil.rmtree(work, ignore_errors=True)
