#!/usr/bin/env python
from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from dataclasses import dataclass
from typing import Set, List, Callable, Optional, Dict

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "StraightlineVaultBot/1.0"
DEFAULT_TIMEOUT = 20
DEFAULT_DELAY_SECONDS = 2.0  # per domain


@dataclass
class CrawlConfig:
    max_depth: int = 1
    same_domain_only: bool = True
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    user_agent: str = USER_AGENT


class RobotsCache:
    """
    Simple robots.txt cache using urllib.robotparser.
    """

    def __init__(self):
        self._parsers: Dict[str, urllib.robotparser.RobotFileParser] = {}

    def can_fetch(self, url: str, user_agent: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._parsers.get(base)
        if rp is None:
            robots_url = urllib.parse.urljoin(base, "/robots.txt")
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.set_url(robots_url)
                rp.read()
            except Exception:
                # If robots.txt is unreachable, default to allowing
                logger.debug("Could not read robots.txt at %s", robots_url)
            self._parsers[base] = rp
        try:
            return rp.can_fetch(user_agent, url)
        except Exception:
            # If parser is broken, default to allow (can adjust to deny if you prefer)
            return True


class DomainThrottler:
    """
    Simple per-domain delay controller.
    """

    def __init__(self, delay_seconds: float):
        self.delay = delay_seconds
        self.last_request_time: Dict[str, float] = {}

    def wait(self, url: str):
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        now = time.time()
        last = self.last_request_time.get(domain)
        if last is not None:
            elapsed = now - last
            if elapsed < self.delay:
                sleep_for = self.delay - elapsed
                time.sleep(sleep_for)
        self.last_request_time[domain] = time.time()


def fetch_url(url: str, cfg: CrawlConfig, throttler: DomainThrottler) -> Optional[str]:
    throttler.wait(url)
    headers = {"User-Agent": cfg.user_agent}
    try:
        resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        logger.warning("Error fetching %s: %s", url, e)
        return None

    if not (200 <= resp.status_code < 300):
        logger.info("Skipping %s: HTTP %s", url, resp.status_code)
        return None

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        # Not HTML – ingestion pipeline can handle this URL directly
        logger.debug("Non-HTML content at %s (Content-Type=%s)", url, content_type)
        return None

    return resp.text


def extract_links(base_url: str, html: str, same_domain_only: bool = True) -> List[str]:
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = parsed_base.netloc

    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#"):
            continue

        abs_url = urllib.parse.urljoin(base_url, href)
        parsed_abs = urllib.parse.urlparse(abs_url)

        if parsed_abs.scheme not in ("http", "https"):
            continue

        if same_domain_only and parsed_abs.netloc != base_domain:
            continue

        links.append(abs_url)

    # De-duplicate while preserving order
    seen = set()
    deduped: List[str] = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return deduped


def crawl_seed_urls(
    seed_urls: List[str],
    cfg: Optional[CrawlConfig] = None,
    on_document: Optional[Callable[[str, Optional[str]], None]] = None,
):
    """
    Crawl starting from seed URLs.

    - Respects robots.txt.
    - Only follows same-domain links if cfg.same_domain_only.
    - Depth = 0 means just the seed URLs themselves.
    - For non-HTML resources (PDF, etc.), html will be None
      and you should rely on your ingestion pipeline to handle the URL.
    """
    if cfg is None:
        cfg = CrawlConfig()

    if on_document is None:
        # Default: just log
        def on_document(url: str, html: Optional[str]):
            logger.info("Visited %s", url)

    robots = RobotsCache()
    throttler = DomainThrottler(delay_seconds=cfg.delay_seconds)
    visited: Set[str] = set()

    queue = deque()
    for u in seed_urls:
        queue.append((u, 0))

    while queue:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        if not robots.can_fetch(url, cfg.user_agent):
            logger.info("Disallowed by robots.txt: %s", url)
            continue

        logger.info("Crawling %s (depth %d)", url, depth)
        html = fetch_url(url, cfg, throttler)

        # Hand off to ingestion / processing
        try:
            on_document(url, html)
        except Exception as e:
            logger.error("Error in on_document for %s: %s", url, e)

        # If we've hit depth limit or no HTML, don't extract links
        if html is None or depth >= cfg.max_depth:
            continue

        child_links = extract_links(url, html, same_domain_only=cfg.same_domain_only)
        for child_url in child_links:
            if child_url not in visited:
                queue.append((child_url, depth + 1))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    def demo_ingest(url: str, html: Optional[str]):
        print(f"INGEST: {url} (html={'yes' if html else 'no'})")

    seeds = [
        "https://www.miamiherald.com/news/local/article214210674.html",
    ]
    crawl_seed_urls(seeds, cfg=CrawlConfig(max_depth=1), on_document=demo_ingest)
