#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List

import requests
from bs4 import BeautifulSoup

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault_core.ingest.pipeline import ingest_source  # type: ignore[import]


DUCKDUCKGO_SEARCH_URL = "https://duckduckgo.com/html/"


def fetch_pdf_urls(query: str, limit: int = 5) -> List[str]:
    """
    Use DuckDuckGo HTML results to find PDF URLs for a given query.
    This is a lightweight, best-effort approach – HTML structure may change.
    """
    params = {
        "q": f"{query} filetype:pdf",
    }
    headers = {
        "User-Agent": "StraightlineVaultBot/0.1 (+non-malicious investigative use)"
    }

    resp = requests.get(DUCKDUCKGO_SEARCH_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    urls: List[str] = []

    # DuckDuckGo's HTML can change, so we try a few patterns.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower():
            # Strip tracking params if present
            # Many DDG links look like: "/l/?kh=-1&uddg=<urlencoded>"
            if href.startswith("/"):
                # Leave it – we can't easily resolve without their redirect,
                # but often DDG will include the final URL in `uddg=`.
                # For now, skip these to avoid complexity.
                continue
            if href not in urls:
                urls.append(href)
        if len(urls) >= limit:
            break

    return urls


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the web for PDFs matching a query and ingest them into the Vault."
    )
    parser.add_argument(
        "query",
        help="Search query (e.g. 'IRS travel expenses 2023', 'Epstein court filings').",
    )
    parser.add_argument(
        "--case",
        help="Case identifier to tag these ingests with (e.g. irs_travel, epstein_web).",
        required=True,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of PDFs to ingest (default: 5).",
    )

    args = parser.parse_args()

    print(f"🌐 Searching web for PDFs matching: {args.query!r}")
    try:
        urls = fetch_pdf_urls(args.query, limit=args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] web search failed: {e}", file=sys.stderr)
        raise SystemExit(1)

    if not urls:
        print("No PDF URLs found in search results.")
        raise SystemExit(0)

    print(f"Found {len(urls)} PDF URL(s):")
    for u in urls:
        print(f"  - {u}")

    print("\n📥 Ingesting into case:", args.case)
    ingested = 0

    for url in urls:
        try:
            print(f"\n[INGEST] {url}")
            pdf_path, txt_path = ingest_source(url, case=args.case)
            print("  ✔ Ingested")
            print(f"    pdf: {pdf_path}")
            print(f"    txt: {txt_path}")
            ingested += 1
            # Tiny pause to be polite to remote servers
            time.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            print(f"  [ERROR] ingest failed for {url}: {e}", file=sys.stderr)

    print(f"\n✅ Done. Successfully ingested {ingested} document(s) into case {args.case!r}.")
    print("👉 Now run: python -m scripts.index_docs  (if you want them searchable immediately.)")


if __name__ == "__main__":
    main()
