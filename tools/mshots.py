"""Mobile screenshots (390x844) of the mobile web build served at /m/."""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/m/"
OUT = Path(__file__).parent / "smoke" / "shots" / "mobile"
OUT.mkdir(parents=True, exist_ok=True)
routes = sys.argv[1:] or ["", "decisions"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="zh-CN",
                            is_mobile=True, has_touch=True)
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(f"{BASE}#/login", wait_until="networkidle")
    page.screenshot(path=str(OUT / "login.png"))
    page.fill("input[autocomplete=username]", os.environ.get("SHOTS_USER", "demo"))
    page.fill("input[autocomplete=current-password]", os.environ.get("SHOTS_PASSWORD", "Demo-pass-2026"))
    page.click("button[type=submit]")
    page.wait_for_timeout(2500)
    for route in routes:
        page.goto(f"{BASE}#/{route}", wait_until="networkidle")
        page.wait_for_timeout(2000)
        name = route.replace("/", "_") or "home"
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
        print("shot", name)
    print(json.dumps(errors[:20], ensure_ascii=False))
    browser.close()
