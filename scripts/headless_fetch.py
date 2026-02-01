#!/usr/bin/env python
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Optional, Tuple

# ---------------------------------------------------------------------
# Playwright availability (import once)
# ---------------------------------------------------------------------
try:
    from playwright.sync_api import sync_playwright  # type: ignore
    _PLAYWRIGHT_AVAILABLE = True
except Exception:
    _PLAYWRIGHT_AVAILABLE = False


# ---------------------------------------------------------------------
# Runtime environment accessors (NO import-time freezing)
# ---------------------------------------------------------------------
def headless_enabled() -> bool:
    return os.getenv("HEADLESS_FETCH_ENABLED", "0") == "1"


def headless_on_403() -> bool:
    # default 0 unless you really want always-on fallback
    return os.getenv("HEADLESS_FETCH_ON_403", "0") == "1"


def headless_browser() -> str:
    return (os.getenv("HEADLESS_BROWSER", "chromium") or "chromium").strip().lower()


def headless_timeout_ms() -> int:
    try:
        return int(os.getenv("HEADLESS_TIMEOUT_MS", "30000"))
    except Exception:
        return 30000


def headless_wait_until() -> str:
    return (os.getenv("HEADLESS_WAIT_UNTIL", "domcontentloaded") or "domcontentloaded").strip()


def headless_user_agent() -> str:
    return os.getenv(
        "HEADLESS_USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    )


def allowed_domains_set() -> set[str]:
    raw = os.getenv("HEADLESS_ALLOWED_DOMAINS", "") or ""
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


# ---------------------------------------------------------------------
# Allowlist logic
# ---------------------------------------------------------------------
def headless_can_use_for(host: str) -> bool:
    """
    True iff:
      - HEADLESS_FETCH_ENABLED=1
      - Playwright available
      - host matches HEADLESS_ALLOWED_DOMAINS (exact or subdomain)
    """
    if not headless_enabled() or not _PLAYWRIGHT_AVAILABLE:
        return False

    host = (host or "").lower().strip()
    if not host:
        return False

    allowed = allowed_domains_set()
    if not allowed:
        return False

    return any(host == d or host.endswith("." + d) for d in allowed)


# ---------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------
@contextmanager
def _playwright_browser():
    with sync_playwright() as p:
        b = headless_browser()

        if b == "firefox":
            browser = p.firefox.launch(headless=True)
        elif b == "webkit":
            browser = p.webkit.launch(headless=True)
        else:
            browser = p.chromium.launch(headless=True)

        try:
            yield browser
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ---------------------------------------------------------------------
# Headless fetch
# ---------------------------------------------------------------------
def headless_fetch_html(url: str, timeout_ms: Optional[int] = None) -> Tuple[str, str]:
    """
    Fetch (final_url, html_text) using Playwright headless.

    NOTES:
      - This renders JS and extracts DOM; it does not guarantee bypassing anti-bot.
      - SSRF + allowlist are enforced by caller.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright is not installed in this environment")

    timeout_ms = int(timeout_ms or headless_timeout_ms())

    with _playwright_browser() as browser:
        ctx = browser.new_context(
            user_agent=headless_user_agent(),
            viewport={"width": 1365, "height": 768},
            locale="en-US",
        )
        page = ctx.new_page()

        page.goto(url, timeout=timeout_ms, wait_until=headless_wait_until())

        # Small settle delay to avoid half-rendered DOM
        try:
            page.wait_for_timeout(750)
        except Exception:
            pass

        html = page.content()
        final_url = page.url

        try:
            ctx.close()
        except Exception:
            pass

        return final_url, html


# ---------------------------------------------------------------------
# Backwards-compatible exports
# ---------------------------------------------------------------------
# IMPORTANT: These are snapshots for older imports. Prefer calling the functions
# (headless_enabled/headless_on_403/allowed_domains_set) for runtime correctness.
HEADLESS_ENABLED = headless_enabled()
HEADLESS_ON_403 = headless_on_403()
HEADLESS_ALLOWED_DOMAINS = allowed_domains_set()
