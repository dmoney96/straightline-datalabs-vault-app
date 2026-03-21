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


# ------------- Provider helpers -------------


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
    Query SerpAPI.

    Env:
      - SERPAPI_API_KEY   (required)
      - SERPAPI_ENGINE    (optional: 'google' (default) or 'duckduckgo')
      - USE_SERPAPI       (optional: '0' to disable quickly)

    Raises MetasearchError if not configured or if the API call fails.
    """
    if os.getenv("USE_SERPAPI", "1") == "0":
        raise MetasearchError("SerpAPI disabled via USE_SERPAPI=0")

    key = os.getenv("SERPAPI_API_KEY")
    if not key:
        raise MetasearchError("SERPAPI_API_KEY not set")

    engine = os.getenv("SERPAPI_ENGINE", "google").strip().lower() or "google"
    if engine not in {"google", "duckduckgo"}:
        logger.warning(
            "SERPAPI_SEARCH: unsupported SERPAPI_ENGINE=%r, defaulting to 'google'",
            engine,
        )
        engine = "google"

    max_results = max(1, min(max_results, 20))

    endpoint = "https://serpapi.com/search.json"
    params = {
        "q": query,
        "engine": engine,
        "num": max_results,
        "api_key": key,
    }

    logger.warning(
        "SERPAPI_SEARCH: calling SerpAPI: engine=%s endpoint=%s num=%d",
        engine,
        endpoint,
        max_results,
    )

    try:
        resp = requests.get(endpoint, params=params, timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("SERPAPI_SEARCH: request failed: %r", e)
        raise MetasearchError(f"SerpAPI request failed: {e!r}") from e

    # Different engines structure their payload differently; organic_results is common
    organic = data.get("organic_results") or data.get("results") or []

    results: List[SearchResult] = []
    for r in organic:
        url = (
            r.get("link")
            or r.get("url")
            or r.get("source")
        )
        if not url:
            continue
        title = r.get("title") or url
        snippet = r.get("snippet") or r.get("description") or ""
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider=f"serpapi:{engine}",
            )
        )

    logger.warning("SERPAPI_SEARCH: got %d result(s)", len(results))
    return results


def _google_cse_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Google Custom Search via JSON API.

    Env:
      - GOOGLE_API_KEY
      - GOOGLE_CSE_CX

    Raises MetasearchError if not configured or if the API call fails.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_CX")
    if not api_key or not cx:
        raise MetasearchError("GOOGLE_API_KEY or GOOGLE_CSE_CX not set")

    max_results = max(1, min(max_results, 10))  # Google CSE 'num' max is 10/request

    endpoint = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": max_results,
    }

    logger.warning(
        "GOOGLE_CSE: calling Google CSE: endpoint=%s num=%d", endpoint, max_results
    )

    try:
        resp = requests.get(endpoint, params=params, timeout=(5, 10))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("GOOGLE_CSE: request failed: %r", e)
        raise MetasearchError(f"Google CSE request failed: {e!r}") from e

    items = data.get("items") or []

    results: List[SearchResult] = []
    for item in items:
        url = item.get("link")
        if not url:
            continue
        title = item.get("title") or url
        snippet = item.get("snippet") or ""
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="google_cse",
            )
        )

    logger.warning("GOOGLE_CSE: got %d result(s)", len(results))
    return results


# Map provider id -> callable so we can configure order via env
_PROVIDER_FUNCS = {
    "brave": _brave_search,
    "serpapi": _serpapi_search,
    "google_cse": _google_cse_search,
}


def _run_provider(name: str, query: str, max_results: int) -> List[SearchResult]:
    fn = _PROVIDER_FUNCS.get(name)
    if fn is None:
        raise MetasearchError(f"Unknown provider {name!r}")
    return fn(query, max_results=max_results)


def metasearch(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Simple hybrid metasearch with configurable provider order.

    Env influence:

      PRIMARY_SEARCH_PROVIDER   (default: 'brave')
      SECONDARY_SEARCH_PROVIDER (default: 'serpapi')
      TERTIARY_SEARCH_PROVIDER  (default: 'google_cse')
      SEARCH_MAX_RESULTS        (optional global clamp)

    Provider names understood: 'brave', 'serpapi', 'google_cse'

    Behaviour:
      - We walk providers in the configured order.
      - First provider that returns *any* results wins.
      - If all fail or return nothing, MetasearchError is raised.
    """
    # Respect a global clamp, but never exceed per-provider caps
    try:
        global_max = int(os.getenv("SEARCH_MAX_RESULTS", str(max_results)))
    except ValueError:
        global_max = max_results

    max_results = max(1, min(max_results, global_max, 20))

    order = []
    primary = os.getenv("PRIMARY_SEARCH_PROVIDER", "brave").strip().lower()
    secondary = os.getenv("SECONDARY_SEARCH_PROVIDER", "serpapi").strip().lower()
    tertiary = os.getenv("TERTIARY_SEARCH_PROVIDER", "google_cse").strip().lower()

    for name in (primary, secondary, tertiary):
        if name and name in _PROVIDER_FUNCS and name not in order:
            order.append(name)

    # If env vars are nonsense, fallback to a safe default sequence
    if not order:
        order = ["brave", "serpapi", "google_cse"]

    logger.warning("META: provider order=%s max_results=%d", order, max_results)

    last_error: Exception | None = None

    for provider_name in order:
        try:
            results = _run_provider(provider_name, query, max_results)
        except MetasearchError as e:
            logger.warning("META: provider %s unavailable: %r", provider_name, e)
            last_error = e
            continue
        except Exception as e:
            logger.warning("META: unexpected error from %s: %r", provider_name, e)
            last_error = e
            continue

        if results:
            logger.warning(
                "META: returning %d result(s) from %s", len(results), provider_name
            )
            return results

    # Nothing worked
    if last_error:
        raise MetasearchError(f"No provider returned results; last error: {last_error}")
    raise MetasearchError("No search provider returned any results")
