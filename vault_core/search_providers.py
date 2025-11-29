from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


@dataclass
class SearchResult:
    """
    Minimal search result shape exposed to callers.

    The rest of your app only *needs* .url, but we also
    carry title/snippet for future UI use if desired.
    """
    url: str
    title: str | None = None
    snippet: str | None = None
    provider: str | None = None


class MetasearchError(RuntimeError):
    pass


# Global timeout knobs for all providers (seconds)
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
PROVIDER_TIMEOUT = 8.0          # max time we'll wait per provider
GLOBAL_TIMEOUT_MARGIN = 2.0     # global wait for as_completed


def _brave_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Brave Search API.
    Requires env BRAVE_API_KEY set to a Brave subscription token.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        # Not configured; just opt-out gracefully
        return []

    endpoint = "https://api.search.brave.com/res/v1/web/search"

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
        "User-Agent": "StraightlineVault/0.1 (+non-malicious investigative use)",
    }

    params = {
        "q": query,
        "count": max_results,
        # You can tune these:
        # "country": "us",
        # "safesearch": "off",
    }

    resp = requests.get(
        endpoint,
        headers=headers,
        params=params,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError as e:
        raise MetasearchError(f"Brave Search returned non-JSON: {e}") from e

    web = data.get("web", {})
    items = web.get("results") or []

    results: List[SearchResult] = []
    for item in items:
        url = item.get("url")
        if not url:
            continue
        title = item.get("title")
        snippet = item.get("description") or item.get("snippet")
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="brave",
            )
        )
    return results


def _google_cse_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    Google Programmable Search Engine (Custom Search).
    Requires:
      - env GOOGLE_API_KEY
      - env GOOGLE_CSE_CX
    If either is missing, this provider is silently disabled.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_CX")

    if not api_key or not cx:
        return []

    endpoint = "https://www.googleapis.com/customsearch/v1"

    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": min(max_results, 10),  # Google caps at 10 per request
    }

    resp = requests.get(
        endpoint,
        params=params,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError as e:
        raise MetasearchError(f"Google CSE returned non-JSON: {e}") from e

    items = data.get("items") or []

    results: List[SearchResult] = []
    for item in items:
        url = item.get("link")
        if not url:
            continue
        title = item.get("title")
        snippet = item.get("snippet")
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="google_cse",
            )
        )

    return results


def _serpapi_search(query: str, max_results: int = 10) -> List[SearchResult]:
    """
    SerpAPI as another provider.
    Requires env SERPAPI_API_KEY.
    You can tweak engine/location as needed.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return []

    endpoint = "https://serpapi.com/search"

    params = {
        "api_key": api_key,
        "engine": "google",
        "q": query,
        "num": max_results,
    }

    resp = requests.get(
        endpoint,
        params=params,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError as e:
        raise MetasearchError(f"SerpAPI returned non-JSON: {e}") from e

    items = data.get("organic_results") or []

    results: List[SearchResult] = []
    for item in items:
        url = item.get("link")
        if not url:
            continue
        title = item.get("title")
        snippet = item.get("snippet") or item.get("description")
        results.append(
            SearchResult(
                url=url,
                title=title,
                snippet=snippet,
                provider="serpapi",
            )
        )

    return results


def _enabled_providers():
    """
    Decide which providers are *logically* enabled based on env vars.
    This keeps metasearch a true 'meta' layer.
    """
    providers = []

    # Brave
    if os.getenv("BRAVE_API_KEY"):
        providers.append(("brave", _brave_search))

    # Google CSE
    if os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_CX"):
        providers.append(("google_cse", _google_cse_search))

    # SerpAPI
    if os.getenv("SERPAPI_API_KEY"):
        providers.append(("serpapi", _serpapi_search))

    return providers


def metasearch(query: str, max_results: int = 10) -> Iterable[SearchResult]:
    """
    Public metasearch entry point.

    - Fans out to all configured providers in parallel.
    - Enforces per-provider timeouts.
    - Deduplicates by URL.
    - Returns up to `max_results` SearchResult objects.

    Raises MetasearchError if *no* providers are configured,
    or all providers fail / return nothing.
    """
    query = (query or "").strip()
    if not query:
        return []

    providers = _enabled_providers()
    if not providers:
        raise MetasearchError(
            "No web search providers configured. Set BRAVE_API_KEY and/or "
            "GOOGLE_API_KEY + GOOGLE_CSE_CX and/or SERPAPI_API_KEY."
        )

    results: List[SearchResult] = []
    seen_urls: set[str] = set()

    # Submit all providers in parallel with their own internal HTTP timeouts.
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        future_to_name = {
            pool.submit(_run_provider_safe, name, func, query, max_results): name
            for (name, func) in providers
        }

        # Global timeout guard so we don't hang forever even if a provider ignores timeouts.
        try:
            for fut in as_completed(
                future_to_name, timeout=PROVIDER_TIMEOUT + GLOBAL_TIMEOUT_MARGIN
            ):
                try:
                    provider_results = fut.result()
                except MetasearchError:
                    # Already logged inside _run_provider_safe
                    continue
                except Exception as e:
                    # Shouldn't usually happen, but don't let it crash the meta layer
                    print(f"[metasearch] provider {future_to_name[fut]} raised: {e}")
                    continue

                for r in provider_results:
                    if r.url in seen_urls:
                        continue
                    seen_urls.add(r.url)
                    results.append(r)
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break
        except Exception as e:
            # If as_completed times out or something odd happens
            print(f"[metasearch] global timeout or error: {e}")

    if not results:
        raise MetasearchError("All web search providers failed or returned no results.")

    return results


def _run_provider_safe(
    name: str,
    func,
    query: str,
    max_results: int,
) -> List[SearchResult]:
    """
    Helper to run a provider with a per-provider timeout and
    nice error reporting.
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(func, query, max_results)
            return future.result(timeout=PROVIDER_TIMEOUT)
    except Exception as e:
        print(f"[metasearch] provider {name} error: {e}")
        raise MetasearchError(f"Provider {name} failed: {e}") from e
