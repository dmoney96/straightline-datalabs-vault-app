#!/usr/bin/env python
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Tuple

# Feature flags from environment
HEADLESS_ENABLED = os.getenv("HEADLESS_FETCH_ENABLED", "0") == "1"
HEADLESS_ON_403 = os.getenv("HEADLESS_FETCH_ON_403", "1") == "1"

_allowed = os.getenv("HEADLESS_ALLOWED_DOMAINS", "") or ""
HEADLESS_ALLOWED_DOMAINS = {
    d.strip().lower() for d in _allowed.split(",") if d.strip()
}

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import]
    _PLAYWRIGHT_AVAILABLE = True
except Exception:
    _PLAYWRIGHT_AVAILABLE = False


def headless_can_use_for(host: str) -> bool:
    """
    Return True if we are allowed to do a headless fetch for this host.

    Conditions:
      - HEADLESS_FETCH_ENABLED=1
      - Playwright successfully imported
      - host matches HEADLESS_ALLOWED_DOMAINS (exact or subdomain).
    """
    if not HEADLESS_ENABLED or not _PLAYWRIGHT_AVAILABLE:
        return False

    host = (host or "").lower()
    if not HEADLESS_ALLOWED_DOMAINS:
        # Be paranoid: if no allowlist is configured, do not run headless.
        return False

    return any(
        host == d or host.endswith("." + d)
        for d in HEADLESS_ALLOWED_DOMAINS
    )


@contextmanager
def _playwright_browser():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def headless_fetch_html(url: str, timeout_ms: int = 20000) -> Tuple[str, str]:
    """
    Fetch (final_url, html_text) using Firefox headless via Playwright.

    This is intended ONLY for public, non-authenticated pages.
    Do NOT use for login-only or paywalled content.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright is not installed in this environment.")

    with _playwright_browser() as browser:
        page = browser.new_page()
        page.goto(url, timeout=timeout_ms, wait_until="networkidle")
        html = page.content()
        final_url = page.url
        return final_url, html
