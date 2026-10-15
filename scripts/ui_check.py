"""Opens the built UI in headless Chromium, walks the main screens, saves screenshots, reports console errors."""
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

projects = httpx.get(BASE + "/api/projects").json()
pid = next((p["id"] for p in projects if p["current_task"]), projects[0]["id"] if projects else None)

errors: list[str] = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 950})
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}"))

    page.goto(BASE + "/#/projects")
    page.wait_for_selector("text=Projects")
    page.wait_for_selector("text=calc-demo", timeout=60000)  # the list runs a git status per project; wait for it
    # for the published screenshot, hide throw-away acceptance projects whose temp paths contain the local Windows user name
    page.evaluate("""() => document.querySelectorAll('div.card').forEach(c => { if (c.textContent.includes('AppData')) c.style.display = 'none'; })""")
    time.sleep(0.5)
    page.screenshot(path=str(OUT / "1-dashboard.png"))

    if pid:
        page.goto(f"{BASE}/#/workspace/{pid}")
        page.wait_for_selector("text=Explorer")
        time.sleep(1.5)
        page.click("text=calc.py") if page.query_selector("text=calc.py") else None
        time.sleep(2.5)  # monaco
        page.screenshot(path=str(OUT / "2-workspace.png"))
        for tab in ("Tools", "Tests", "Tasks", "Checkpoints", "Memory"):
            page.click(f"button:has-text('{tab}')")
            time.sleep(0.5)
            page.screenshot(path=str(OUT / f"3-tab-{tab.lower()}.png"))

    for t, name in (("model", "model"), ("agent", "agent"), ("autonomy", "autonomy"), ("memory", "memory")):
        page.goto(f"{BASE}/#/settings/{t}")
        time.sleep(1.0)
        page.screenshot(path=str(OUT / f"4-settings-{name}.png"))
    b.close()

print("console/page errors:", errors or "none")
sys.exit(1 if errors else 0)
