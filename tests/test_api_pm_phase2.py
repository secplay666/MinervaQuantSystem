"""Position manager API, phase 2: zones, sentinels, the automatic base switch, tiers, pools, lists."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

pytest.importorskip("fastapi")

from quant_system.app.db.models import PmSentinel, PmSettings, PmSignal  # noqa: E402
from test_api_pm import DAYS, add, item_id, level, pm  # noqa: E402,F401  (fixture)

LAST = DAYS[-1].date()


def test_a_buyback_zone_needs_both_edges_and_a_share(pm) -> None:
    client, _, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    bad = client.post(f"/api/v1/pm/items/{item}/levels", json={"kind": "buyback_zone", "price": 16.0}, headers=alice)
    assert bad.status_code == 400
    detail = level(client, alice, item, kind="buyback_zone", price=16.0, lower=14.5, fraction=0.4)
    zone = next(lv for lv in detail["levels"] if lv["kind"] == "buyback_zone")
    assert zone["price"] == pytest.approx(16.0) and zone["lower"] == pytest.approx(14.5) and zone["fraction"] == 0.4


def test_sentinels_are_limited_checked_reset_and_removed(pm) -> None:
    client, sessions, alice, bob = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    url = f"/api/v1/pm/items/{item}/sentinels"
    wrong = client.post(url, json={"price": 17.0, "direction": "up"}, headers=alice)  # the close is 18: below it
    assert wrong.status_code == 400 and "高于现价" in wrong.json()["detail"]["message"]
    detail = client.post(url, json={"price": 19.0, "direction": "up", "source_ref": "前高"}, headers=alice).json()
    sentinel = detail["item"]["sentinels"][0]
    assert sentinel["price"] == pytest.approx(19.0) and sentinel["distance"] == pytest.approx(19 / 18 - 1)
    assert sentinel["days"] >= 1 and sentinel["status"] == "active"
    client.post(url, json={"price": 16.0, "direction": "down"}, headers=alice)
    assert client.post(url, json={"price": 15.0, "direction": "down"}, headers=alice).status_code == 400  # at most 2
    assert client.patch(f"/api/v1/pm/sentinels/{sentinel['id']}", json={"price": 20.0}, headers=bob).status_code == 404
    detail = client.patch(f"/api/v1/pm/sentinels/{sentinel['id']}", json={"price": 20.0}, headers=alice).json()
    moved = next(s for s in detail["item"]["sentinels"] if s["id"] == sentinel["id"])
    assert moved["price"] == pytest.approx(20.0) and moved["version"] == 2
    chart = client.get("/api/v1/pm/chart/600000", headers=alice).json()
    assert len(chart["sentinels"]) == 2
    detail = client.delete(f"/api/v1/pm/sentinels/{sentinel['id']}", headers=alice).json()
    assert [s["direction"] for s in detail["item"]["sentinels"]] == ["down"]


def test_a_crossing_reminds_once_and_never_changes_the_label(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    client.post(f"/api/v1/pm/items/{item}/sentinels", json={"price": 19.0, "direction": "up"}, headers=alice)
    with sessions() as session:  # as if set a while ago, below today's close of 18 it went through on the way up
        row = session.scalar(select(PmSentinel))
        row.price, row.effective_date = 34.0, DAYS[-30].date()  # hfq: 17 x 2
        session.scalar(select(PmSettings)).evaluated_through = DAYS[-30].date()  # last evaluated back then
        session.commit()
    board = client.get("/api/v1/pm/board", headers=alice).json()
    assert board["new_signals"] == 1 and board["signals"][0]["rule"] == "sentinel"
    assert "是否更新方向标签" in board["signals"][0]["message"]
    assert board["items"][0]["label"] == "right" and board["items"][0]["sentinels"][0]["status"] == "crossed"
    assert client.get("/api/v1/pm/board", headers=alice).json()["new_signals"] == 0


def test_a_close_in_the_base_zone_turns_left_into_base_automatically(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "left"}, headers=alice)
    level(client, alice, item, kind="base_zone", price=19.0, lower=17.0)  # today's close 18 is inside
    board = client.get("/api/v1/pm/board", headers=alice).json()
    assert [s["rule"] for s in board["signals"]] == ["base_zone"]
    detail = client.get(f"/api/v1/pm/items/{item}", headers=alice).json()
    assert detail["item"]["label"] == "base" and detail["item"]["label_source"] == "auto"
    assert detail["label_history"][0]["source"] == "auto" and detail["label_history"][0]["created_by"] == "system"

    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "left"}, headers=alice)
    client.put("/api/v1/pm/settings", json={"auto_base": False}, headers=alice)
    level(client, alice, item, kind="base_zone", price=19.5, lower=17.0)  # a new version prompts again
    client.get("/api/v1/pm/board", headers=alice)
    assert client.get(f"/api/v1/pm/items/{item}", headers=alice).json()["item"]["label"] == "left"


def test_the_board_has_pools_and_five_tiers(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000 sh000300")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    level(client, alice, item, kind="neckline", price=11.0)
    level(client, alice, item, kind="target", price=20.0)
    board = client.get("/api/v1/pm/board", headers=alice).json()
    assert board["pools"] == {"hold": 1, "buyback": 0, "ready": 0, "watch": 1}
    assert [t["name"] for t in board["tiers"]] == ["今日触发", "临门一脚", "逼近中", "有变化", "其余"]
    first = board["tiers"][0]["items"]
    assert [i["symbol"] for i in first] == ["600000"] and "入场" in " ".join(first[0]["messages"])
    stock = next(r for r in board["items"] if r["symbol"] == "600000")
    assert stock["pool"] == "hold" and stock["entered"] and stock["breakout_close"] == pytest.approx(18.0)


def test_breakouts_tops_and_the_campaign_ledger(pm) -> None:
    client, sessions, alice, _ = pm
    add(client, alice, "600000")
    item = item_id(client, alice, "600000")
    client.put(f"/api/v1/pm/items/{item}/label", json={"label": "right"}, headers=alice)
    level(client, alice, item, kind="neckline", price=11.0)
    level(client, alice, item, kind="target", price=20.0)
    lists = client.get("/api/v1/pm/lists/breakouts", headers=alice).json()
    assert [r["symbol"] for r in lists["resonant"]] == ["600000"] and lists["waiting"] == []
    level(client, alice, item, kind="top_neckline", price=17.0, top_mode="observe")
    tops = client.get("/api/v1/pm/lists/tops", headers=alice).json()
    assert tops["observing"][0]["top_neckline"] == pytest.approx(17.0)
    assert tops["observing"][0]["distance"] == pytest.approx(17 / 18 - 1)
    ledger = client.get("/api/v1/pm/campaigns", headers=alice).json()
    assert ledger["totals"]["rounds"] == 1 and ledger["totals"]["open"] == 1 and ledger["rows"][0]["open"]
    with sessions() as session:
        assert session.scalar(select(PmSignal).where(PmSignal.rule == "entry")) is None  # no board visit yet
    assert ledger["rows"][0]["segments"][0]["entry_date"] == LAST.isoformat()
    assert date.fromisoformat(lists["latest"]) == LAST
