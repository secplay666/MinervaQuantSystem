"""Log in to the local dev frontend and screenshot pages (visual check during development)."""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import os

BASE = os.environ.get("SHOTS_BASE", "http://localhost:5666")
HASH = "/#" if os.environ.get("SHOTS_HASH") else ""
OUT = Path(__file__).parent / "smoke" / "shots"
OUT.mkdir(parents=True, exist_ok=True)
pages = ["/" + a.lstrip("/") for a in sys.argv[1:]] or ["/home", "/decisions"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error",) else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(f"{BASE}{HASH}/auth/login", wait_until="networkidle", timeout=120_000)
    page.screenshot(path=str(OUT / "login.png"))
    page.fill("input[autocomplete=username]", os.environ.get("SHOTS_USER", "demo"))
    page.fill("input[autocomplete=current-password]", os.environ.get("SHOTS_PASSWORD", "Demo-pass-2026"))
    page.keyboard.press("Enter")
    page.wait_for_timeout(4000)
    for path in pages:
        page.goto(f"{BASE}{HASH}{path}", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2500)
        name = path.strip("/").replace("/", "_").replace("?", "_").replace("=", "-") or "root"
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        print("shot", name, page.url)
    print(json.dumps(errors[:30], ensure_ascii=False, indent=1))
    browser.close()
