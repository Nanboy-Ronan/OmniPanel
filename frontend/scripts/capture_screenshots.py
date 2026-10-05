"""Capture README panels from the local synthetic preview, never production.

Run `npm --prefix frontend run build`, start the fixture server with
`node frontend/scripts/preview-fixtures.mjs`, then run this file with a Python
environment containing Playwright and its Chromium browser.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PANELS = {
    "overview": "overview",
    "analysis": "analysis&start=2026-09-04&end=2026-10-03",
    "content": "xhs&start=2026-09-04&end=2026-10-03",
    "pgy": "pgy",
    "database": "database",
    "cohort": "retention",
    "identity": "identity",
    "sql": "sql",
}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 1350},
            device_scale_factor=1,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            reduced_motion="reduce",
        )
        context.add_init_script(
            "sessionStorage.setItem('omnipanel.console.token', 'synthetic-preview-only')"
        )
        for name, query in PANELS.items():
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:5180/console/?page={query}", wait_until="networkidle")
            page.get_by_text("本地预览 · 全部为模拟数据", exact=True).wait_for()
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(1200)
            assert not errors, f"{name}: {errors}"
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            ), f"{name}: horizontal overflow"
            path = ROOT / "docs/images" / f"screenshot_{name}.png"
            page.screenshot(path=str(path), animations="disabled")
            print(f"Captured {path.relative_to(ROOT)}")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
