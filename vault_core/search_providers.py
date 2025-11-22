#!/usr/bin/env python
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import requests

logger = logging.getLogger(__name__)


# ---------- Data model ----------

@dataclass
class SearchResult:
    provider: str
    query: str
    title: str
    url: str
    snippet: str
    rank: int
    raw: Dict[str, Any]


# ---------- Base class ----------

class BaseSearchProvider:
    name: str = "base"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        raise NotImplementedError


# ---------- Brave Search Provider ----------

class BraveSearchProvider(BaseSearchProvider):
    """
    Uses Brave Search API.
    Env var: BRAVE_SEARCH_API_KEY
    Docs: https://api.search.brave.com/app/documentation
    """
    name = "brave"

    def __init__(self, api_key: Optional[str] = None):
        # Accept either BRAVE_SEARCH_API_KEY (preferred) or BRAVE_API_KEY (fallback)
        self.api_key = (
            api_key
            or os.getenv("BRAVE_SEARCH_API_KEY")
            or os.getenv("BRAVE_API_KEY")
        )
        if not self.api_key:
            raise RuntimeError("BRAVE_SEARCH_API_KEY (or BRAVE_API_KEY) is not set")

        self.endpoint = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        params = {
            "q": query,
            "count": max_results,
        }
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
            "User-Agent": "StraightlineVault/0.1",
        }

        resp = requests.get(self.endpoint, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        web_results = data.get("web", {}).get("results", [])
        results: List[SearchResult] = []

        for idx, item in enumerate(web_results[:max_results], start=1):
            results.append(
                SearchResult(
                    provider=self.name,
                    query=query,
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", "") or item.get("snippet", ""),
                    rank=idx,
                    raw=item,
                )
            )

        return results


# ---------- Google Programmable Search Provider ----------

class GoogleCSEProvider(BaseSearchProvider):
    """
    Uses Google Custom/Programmable Search JSON API.
    Env vars: GOOGLE_API_KEY, GOOGLE_CSE_ID
    Docs: https://developers.google.com/custom-search/v1/overview
    """
    name = "google_cse"

    def __init__(
        self,
        api_key: Optional[str] = None,
        cse_id: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.cse_id = cse_id or os.getenv("GOOGLE_CSE_ID")

        if not self.api_key or not self.cse_id:
            raise RuntimeError("GOOGLE_API_KEY or GOOGLE_CSE_ID not set")

        self.endpoint = "https://www.googleapis.com/customsearch/v1"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": query,
            "num": min(max_results, 10),  # Google max per request is 10
        }

        resp = requests.get(self.endpoint, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", []) or []
        results: List[SearchResult] = []

        for idx, item in enumerate(items[:max_results], start=1):
            results.append(
                SearchResult(
                    provider=self.name,
                    query=query,
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    rank=idx,
                    raw=item,
                )
            )

        return results


# ---------- SerpAPI Provider (Google wrapper) ----------

class SerpAPIProvider(BaseSearchProvider):
    """
    Uses SerpAPI (Google wrapper).
    Env var: SERPAPI_KEY
    Docs: https://serpapi.com/
    """
    name = "serpapi"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SERPAPI_KEY")
        if not self.api_key:
            raise RuntimeError("SERPAPI_KEY is not set")

        self.endpoint = "https://serpapi.com/search.json"

    def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        params = {
            "engine": "google",
            "q": query,
            "num": min(max_results, 10),
            "api_key": self.api_key,
        }

        resp = requests.get(self.endpoint, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("organic_results", []) or []
        results: List[SearchResult] = []

        for idx, item in enumerate(items[:max_results], start=1):
            results.append(
                SearchResult(
                    provider=self.name,
                    query=query,
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    rank=idx,
                    raw=item,
                )
            )

        return results


# ---------- Metasearch ----------

def metasearch(
    query: str,
    max_results: int = 20,
    providers: Optional[List[BaseSearchProvider]] = None,
) -> List[SearchResult]:
    """
    Run a query across multiple providers and return a merged, de-duplicated list.
    De-dupe is by URL; first occurrence wins.
    """
    query = (query or "").strip()
    if not query:
        return []

    if providers is None:
        providers = []

        # Brave as primary general engine
        try:
            providers.append(BraveSearchProvider())
        except Exception as e:
            logger.warning("Brave provider not available: %s", e)

        # Google CSE optional curated engine
        try:
            providers.append(GoogleCSEProvider())
        except Exception as e:
            logger.warning("Google CSE provider not available: %s", e)

        # SerpAPI as extra Google wrapper
        try:
            providers.append(SerpAPIProvider())
        except Exception as e:
            logger.warning("SerpAPI provider not available: %s", e)

        if not providers:
            raise RuntimeError("No search providers configured")

    seen_urls = set()
    merged: List[SearchResult] = []

    per_provider_target = max(5, max_results // max(1, len(providers)))

    for provider in providers:
        try:
            results = provider.search(query, max_results=per_provider_target)
        except Exception as e:
            logger.error("Error from provider %s: %s", provider.name, e)
            continue

        for res in results:
            if not res.url or not res.url.startswith("http"):
                continue
            if res.url in seen_urls:
                continue
            seen_urls.add(res.url)
            merged.append(res)

            if len(merged) >= max_results:
                break

        if len(merged) >= max_results:
            break

    return merged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    q = "jeffrey epstein court documents pdf"
    for r in metasearch(q, max_results=10):
        print(f"[{r.provider}] {r.title} -> {r.url}")
