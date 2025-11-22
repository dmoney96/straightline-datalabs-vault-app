#!/usr/bin/env python
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# --------------------------------------------------------
# Put project root (~/vault-app) on sys.path
# --------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Now these imports will work even when run as a script
from vault_core.search_providers import metasearch
from vault_core.crawler import crawl_seed_urls, CrawlConfig
from vault_core.ingest.pipeline import ingest_source  # your existing pipeline


logger = logging.getLogger(__name__)


# --------------------------------------------------------
# Ingestion hook – wire search/crawl into your pipeline
# --------------------------------------------------------
def ingest_url(url: str, html: Optional[str]) -> None:
    """
    Very simple ingestion hook.

    Right now:
      - We hand every URL to ingest_source(url, case=None)
      - ingest_source deals with PDFs/URLs the same way as the rest of your Vault

    You can make this smarter later (e.g., treat HTML differently,
    pass a case name, etc.).
    """
    try:
        pdf_path, txt_path = ingest_source(url, case=None)
        logger.info("Ingested %s -> pdf=%s txt=%s", url, pdf_path, txt_path)
    except Exception as e:
        logger.warning("ingest_source failed for %s: %s", url, e)


# --------------------------------------------------------
# Topic harvest orchestration
# --------------------------------------------------------
def run_topic_harvest(query: str, max_search_results: int = 20) -> None:
    """
    1. Use metasearch() to get URLs for the topic.
    2. For each URL, do a tiny same-domain crawl (depth=1).
    3. For every visited URL, call ingest_url().
    """
    logger.info("Running topic harvest for %r", query)

    # Search phase
    results = metasearch(query, max_results=max_search_results)
    seed_urls = [r.url for r in results]
    logger.info("Got %d seed URLs", len(seed_urls))

    if not seed_urls:
        logger.warning("No seed URLs returned for query=%r", query)
        return

    # Crawl config: same-domain only, depth 1
    cfg = CrawlConfig(max_depth=1, same_domain_only=True)

    def _on_document(url: str, html: Optional[str]) -> None:
        logger.info("Visited %s", url)
        ingest_url(url, html)

    # Crawl + ingest
    crawl_seed_urls(seed_urls, cfg=cfg, on_document=_on_document)


# --------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # You can change this query any time
    default_query = "jeffrey epstein indictment pdf"
    logger.info("Starting topic harvest demo for query: %r", default_query)
    run_topic_harvest(default_query, max_search_results=10)
    logger.info("Done.")
