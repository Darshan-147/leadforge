"""Web search with short cache and provider-failure cooldown."""

import logging
from math import ceil
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import urlparse, urlunparse

from ddgs import DDGS
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from models.requests import DiscoverRequest

logger = logging.getLogger("leadforge")

_DISCOVER_CACHE_TTL_SECONDS = 300
_DISCOVER_CACHE_MAX_ENTRIES = 200
_DISCOVER_CACHE: dict[
    tuple[str, str, int],
    tuple[float, list[dict[str, Any]]],
] = {}

_DISCOVER_CACHE_LOCK = Lock()
_SEARCH_BACKOFF_UNTIL = 0.0
_SEARCH_BACKOFF_SECONDS = 30


def search_web(query: str, limit: int, country: str):
    """Search the web with a short cache and provider-failure cooldown."""
    global _SEARCH_BACKOFF_UNTIL

    region = f"{country.lower()}-en"
    cache_key = (query.strip().casefold(), region, int(limit))
    now = monotonic()

    with _DISCOVER_CACHE_LOCK:
        # Avoid calling the provider while it's temporarily failing.
        if now < _SEARCH_BACKOFF_UNTIL:
            retry_after = max(
                1, ceil(_SEARCH_BACKOFF_UNTIL - now)
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "The free search provider is temporarily "
                    f"unavailable. Try again in {retry_after} seconds."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        # Return cached results for identical recent searches.
        cached = _DISCOVER_CACHE.get(cache_key)

        if cached and now - cached[0] < _DISCOVER_CACHE_TTL_SECONDS:
            return [dict(item) for item in cached[1]]

        if cached:
            _DISCOVER_CACHE.pop(cache_key, None)

    try:
        with DDGS(timeout=10) as ddgs:
            results = list(
                ddgs.text(
                    query,
                    region=region,
                    safesearch="moderate",
                    max_results=limit,
                )
            )
    except Exception:
        # Log the real exception on the backend; don't expose
        # internal stack traces or provider details to the client.
        logger.exception("Free web search provider failed")

        with _DISCOVER_CACHE_LOCK:
            _SEARCH_BACKOFF_UNTIL = max(
                _SEARCH_BACKOFF_UNTIL,
                monotonic() + _SEARCH_BACKOFF_SECONDS,
            )

        raise

    with _DISCOVER_CACHE_LOCK:
        _DISCOVER_CACHE[cache_key] = (monotonic(), results)

        # Remove expired entries and keep the cache bounded.
        now = monotonic()
        expired_keys = [
            key
            for key, (timestamp, _) in _DISCOVER_CACHE.items()
            if now - timestamp >= _DISCOVER_CACHE_TTL_SECONDS
        ]

        for key in expired_keys:
            _DISCOVER_CACHE.pop(key, None)

        while len(_DISCOVER_CACHE) > _DISCOVER_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _DISCOVER_CACHE,
                key=lambda key: _DISCOVER_CACHE[key][0],
            )
            _DISCOVER_CACHE.pop(oldest_key, None)

    return [dict(item) for item in results]


async def discover_candidates(request: DiscoverRequest) -> dict[str, Any]:
    """Find and normalize one public website candidate per company domain."""
    query_parts = [request.industry.strip(), "companies official website"]
    if request.location.strip():
        query_parts.append(request.location.strip())
    if request.keywords.strip():
        query_parts.append(request.keywords.strip())
    query = " ".join(query_parts)

    try:
        results = await run_in_threadpool(
            search_web,
            query,
            request.limit,
            request.country,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Discover endpoint failed during web search")
        raise HTTPException(
            status_code=503,
            detail=(
                "The free web-search provider is temporarily unavailable or "
                "rate-limited. Please wait at least 30 seconds before trying again."
            ),
            headers={"Retry-After": "30"},
        ) from exc

    excluded_domains = {
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "twitter.com",
        "x.com",
        "indeed.com",
        "glassdoor.com",
        "wikipedia.org",
    }
    candidates: list[dict[str, str]] = []
    seen_domains: set[str] = set()

    for item in results:
        parsed = urlparse(item.get("href", ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue

        host = parsed.hostname.lower().rstrip(".")
        domain = host[4:] if host.startswith("www.") else host
        if any(
            domain == blocked or domain.endswith("." + blocked)
            for blocked in excluded_domains
        ):
            continue
        if domain in seen_domains:
            continue

        seen_domains.add(domain)
        candidates.append(
            {
                "title": (item.get("title") or domain)[:250],
                "url": urlunparse(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path or "/",
                        "",
                        "",
                        "",
                    )
                ),
                "domain": domain,
                "snippet": (item.get("body") or "")[:500],
            }
        )
        if len(candidates) >= request.limit:
            break

    return {
        "query": query,
        "count": len(candidates),
        "candidates": candidates,
        "message": "Review candidate websites before enriching them.",
    }

