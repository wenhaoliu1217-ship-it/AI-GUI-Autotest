import json
from pathlib import Path

from playwright.sync_api import sync_playwright


output = Path(__file__).parent
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for name, width, height in (("desktop", 1440, 960), ("mobile", 375, 812)):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto("http://127.0.0.1:8081/", wait_until="networkidle")
        page.get_by_role("button", name="\u65b0\u5efa\u6d4b\u8bd5").first.click()
        layout = page.evaluate(
            "() => ({overflow: document.documentElement.scrollWidth > innerWidth, "
            "width: document.documentElement.scrollWidth, viewport: innerWidth})"
        )
        result = {
            "name": name,
            "goal": page.get_by_label("\u5f53\u524d\u6d4b\u8bd5\u76ee\u6807").is_visible(),
            "expected": page.get_by_label("\u671f\u671b\u7ed3\u679c").is_visible(),
            "roleVisible": page.get_by_label("\u6267\u884c\u89d2\u8272").is_visible(),
            "library": page.get_by_text("\u53ef\u590d\u7528\u573a\u666f\u5e93\uff08\u53ef\u9009\uff09").is_visible(),
            **layout,
        }
        page.screenshot(path=output / f"goal-first-{name}.png", full_page=True)
        print(json.dumps(result))
        page.close()
    browser.close()
