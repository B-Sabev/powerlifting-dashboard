"""Drive the running Streamlit dashboard with Playwright and screenshot every tab.

Usage:
    .venv/bin/python dev/screenshot_dashboard.py [--url URL] [--output-dir DIR]

Requires the dashboard already running (see .claude/skills/run-dashboard/SKILL.md
for the full launch/verify/teardown sequence). This script only drives a browser
against an already-listening URL — it does not start or stop the server.

Exits non-zero if any browser console/page error was observed, so it can be used
as a pass/fail check without reading the screenshots back.
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8765")
    parser.add_argument("--output-dir", default="dev/screenshots")
    parser.add_argument("--viewport-width", type=int, default=1400)
    parser.add_argument("--viewport-height", type=int, default=1000)
    # Streamlit runs the script and pushes the render over a websocket after
    # the initial page load — a fixed wait is the simplest reliable way to
    # let that settle (no DOM marker reliably signals "fully rendered").
    parser.add_argument("--render-wait-ms", type=int, default=2500)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": args.viewport_width, "height": args.viewport_height})
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_selector("h1", timeout=30000)
        page.wait_for_timeout(args.render_wait_ms)

        tabs = page.locator('button[role="tab"]')
        count = tabs.count()
        print(f"Found {count} tabs", file=sys.stderr)

        for i in range(count):
            tab = tabs.nth(i)
            label = tab.inner_text()
            tab.click()
            page.wait_for_timeout(args.render_wait_ms)
            safe_label = label.replace(" ", "_").replace("&", "and")
            fname = output_dir / f"tab_{i + 1}_{safe_label}.png"
            page.screenshot(path=str(fname), full_page=True)
            print(f"Screenshotted tab {i + 1}: {label} -> {fname}", file=sys.stderr)

        browser.close()

    if errors:
        print("CONSOLE/PAGE ERRORS:", file=sys.stderr)
        for e in errors:
            print(" -", e, file=sys.stderr)
        return 1

    print("No console/page errors detected.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
