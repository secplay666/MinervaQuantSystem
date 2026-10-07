"""Seed the local demo user's 仓位管家 library through the running API (visual checks only)."""

import json
import os
import urllib.request

BASE = os.environ.get("SHOTS_BASE", "http://127.0.0.1:8000") + "/api/v1"


def call(method: str, path: str, body=None, token: str | None = None):
    request = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read() or b"null")


token = call("POST", "/auth/login", {"username": os.environ.get("SHOTS_USER", "demo"), "password": os.environ.get("SHOTS_PASSWORD", "Demo-pass-2026")})["access_token"]
print(call("POST", "/pm/items", {"text": "600519 000001 300750 600036 sh000300 sh000852 510300 688981", "group": "核心"}, token))
items = {i["symbol"]: i for i in call("GET", "/pm/items", token=token)["items"]}
for symbol, item in items.items():
    if item["stage"] and item["stage"]["stage"] != "unknown":
        call("PUT", f"/pm/items/{item['id']}/label", {"use_view": True}, token)
for symbol, (neck, target) in {"600519": (0.92, 1.25), "000001": (0.95, 1.10), "300750": (1.08, 1.40),
                               "sh000300": (0.97, 1.15)}.items():
    item = items[symbol]
    close = item["close"]
    if symbol in ("600519", "000001"):
        call("PUT", f"/pm/items/{item['id']}/label", {"label": "right"}, token)
    call("POST", f"/pm/items/{item['id']}/levels", {"kind": "neckline", "price": round(close * neck, 2)}, token)
    call("POST", f"/pm/items/{item['id']}/levels", {"kind": "target", "price": round(close * target, 2)}, token)
board = call("GET", "/pm/board", token=token)
print(board["counts"], len(board["items"]), "signals", len(board["signals"]))
for row in board["items"]:
    print(row["symbol"], row["label"], row["phase"], row["completion"], row["waiting_for"])
