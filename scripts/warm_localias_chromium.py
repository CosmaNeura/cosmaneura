from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright


def visit_with_retries(url: str, max_retries: int = 5) -> None:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.goto(url)
                browser.close()
            return
        except Exception as error:  # pragma: no cover - exercised in CI only
            last_error = error
            if attempt < max_retries - 1:
                time.sleep(2)

    raise RuntimeError(f"Chromium could not reach {url}") from last_error


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        raise SystemExit("usage: warm_localias_chromium.py https://host")
    visit_with_retries(sys.argv[1])


if __name__ == "__main__":
    main()
