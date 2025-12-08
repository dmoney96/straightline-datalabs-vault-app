#!/usr/bin/env python
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import List
import requests

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    provider: str


class MetasearchError(Exception):
    """Raised when no search provider can satisfy a query."""
    pass


def _brave_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Query Brave Web Search API.

    Env vars checked (in this order):
      - BRAVE_API_KEY
      - BRAVE_SUBSCRIPTION_TOKEN

    Raises MetasearchError if not configured or if the API call fails.
    """
    key = os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SUBSCRIPTION_TOKEN")
    if not key:
        raise MetasearchError("BRAVE_API_KEY (or BRAVE_SUBSCRIPTION_TOKEN) not set")

    max_results = max(1, min(max_results, 20))

    endpoint = "https://api.search.brave.com/res/v1/web/search"
    params = {
        "q": query,
        "count": max_results,
    }
    headers = {
        "X-Subscription-Token": key,
        "Accept": "application/json",
    }

    logger.warning(
        "BRAVE_SEARCH: calling Brave API: endpoint=%s, count=%d", endpoint, max_results
    )

    try:
        resp = requests.get(endpoint, params=params, headers=headers, timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("BRAVE_SEARCH: request failed: %r", e)
        raise MetasearchError(f"Brave request failed: {e!r}") from e

    web_block = (data.get("web") or {})
    raw_results = web_block.get("results") or []

    results: List[SearchResult] = []
    for r in raw_results:
        url = r.get("url")
        if not url:
            continue
        title = r.get("title") or url
        snippet = (
            r.get("description")
            or r.get("snippet")
            or ""
        )
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="brave",
            )
        )

    logger.warning("BRAVE_SEARCH: got %d result(s)", len(results))
    return results


def _serpapi_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Query SerpAPI directly (Google engine).

    Env var:
      - SERPAPI_API_KEY

    Raises MetasearchError if not configured or if the API call fails.
    """
    key = os.getenv("SERPAPI_API_KEY")
    if not key:
        raise MetasearchError("SERPAPI_API_KEY not set")

    max_results = max(1, min(max_results, 20))

    endpoint = "https://serpapi.com/search.json"
    params = {
        "q": query,
        "engine": "google",
        "num": max_results,
        "api_key": key,
    }

    logger.warning(
        "SERPAPI_SEARCH: calling SerpAPI: endpoint=%s, num=%d", endpoint, max_results
    )

    try:
        resp = requests.get(endpoint, params=params, timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("SERPAPI_SEARCH: request failed: %r", e)
        raise MetasearchError(f"SerpAPI request failed: {e!r}") from e

    organic = data.get("organic_results") or []

    results: List[SearchResult] = []
    for r in organic:
        url = r.get("link") or r.get("url")
        if not url:
            continue
        title = r.get("title") or url
        snippet = r.get("snippet") or ""
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="serpapi",
            )
        )

    logger.warning("SERPAPI_SEARCH: got %d result(s)", len(results))
    return results


def metasearch(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Simple hybrid metasearch:

      1) Try Brave (if configured)
      2) Fallback to SerpAPI (if configured)

    If both fail or return nothing, raises MetasearchError.
    """
    max_results = max(1, min(max_results, 20))

    # 1) Brave first
    try:
        brave_results = _brave_search(query, max_results=max_results)
        if brave_results:
            logger.warning("META: returning %d Brave result(s)", len(brave_results))
            return brave_results
    except MetasearchError as e:
        logger.warning("META: Brave unavailable: %r", e)
    except Exception as e:
        logger.warning("META: unexpected Brave error: %r", e)

    # 2) SerpAPI fallback
    try:
        serp_results = _serpapi_search(query, max_results=max_results)
        if serp_results:
            logger.warning("META: returning %d SerpAPI result(s)", len(serp_results))
            return serp_results
    except MetasearchError as e:
        logger.warning("META: SerpAPI unavailable: %r", e)
    except Exception as e:
        logger.warning("META: unexpected SerpAPI error: %r", e)

    raise MetasearchError("No search provider returned any results")
