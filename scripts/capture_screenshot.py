"""One-off dev script: capture a polished dashboard screenshot for the README."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshot.png"
OUT.parent.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
    page.goto("http://127.0.0.1:5001", wait_until="networkidle")

    page.click("#runBot")
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('実行完了')", timeout=60_000
    )

    page.click("#runBt")
    page.wait_for_function(
        "document.getElementById('btResult').style.display === 'block'", timeout=120_000
    )

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT))
    browser.close()

print(f"saved: {OUT}")
