"""Podcast directory search (Apple iTunes Search API).

Keyless and covers essentially every public podcast. Results are normalized to
a provider-agnostic shape so another directory (Podcast Index etc.) could be
added later without touching the UI.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
RESULT_LIMIT = 25
CACHE_TTL_S = 600
CACHE_SIZE = 100
# Apple documents ~20 requests/minute per IP. Debounced UI + this cache keep
# typical use well under that.
TIMEOUT_S = 10.0


class SearchResult(BaseModel):
    title: str
    author: str | None
    feed_url: str
    artwork_url: str | None
    genre: str | None
    episode_count: int | None
    latest_episode_at: datetime | None
    explicit: bool
    feed_host: str | None


class SearchError(Exception):
    """Directory unreachable or returned garbage."""


def normalize_itunes(raw: dict) -> list[SearchResult]:
    """Turn an iTunes search payload into SearchResults. Entries without a
    feed URL (Apple-hosted subscription-only shows) are dropped, and duplicate
    feeds (Apple sometimes lists the same feed under several IDs) collapse to
    the first occurrence."""
    out: list[SearchResult] = []
    seen: set[str] = set()
    for item in raw.get("results") or []:
        if not isinstance(item, dict):
            continue
        feed_url = (item.get("feedUrl") or "").strip()
        if not feed_url or not feed_url.lower().startswith(("http://", "https://")):
            continue
        if feed_url in seen:
            continue
        seen.add(feed_url)

        title = (item.get("collectionName") or item.get("trackName") or "").strip()
        if not title:
            continue
        latest = _parse_date(item.get("releaseDate"))
        count = item.get("trackCount")
        out.append(
            SearchResult(
                title=title,
                author=(item.get("artistName") or "").strip() or None,
                feed_url=feed_url,
                artwork_url=item.get("artworkUrl600") or item.get("artworkUrl100") or None,
                genre=item.get("primaryGenreName") or None,
                episode_count=int(count) if isinstance(count, int) and count > 0 else None,
                latest_episode_at=latest,
                explicit=item.get("collectionExplicitness") == "explicit",
                feed_host=_host(feed_url),
            )
        )
    return out


def _parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _host(url: str) -> str | None:
    host = urlparse(url).hostname
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


class _Cache:
    """Tiny LRU with TTL, keyed on the normalized query."""

    def __init__(self, size: int, ttl_s: float) -> None:
        self._size = size
        self._ttl = ttl_s
        self._items: OrderedDict[str, tuple[float, list[SearchResult]]] = OrderedDict()

    def get(self, key: str) -> list[SearchResult] | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        stored_at, results = hit
        if time.monotonic() - stored_at > self._ttl:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return results

    def put(self, key: str, results: list[SearchResult]) -> None:
        self._items[key] = (time.monotonic(), results)
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


_cache = _Cache(CACHE_SIZE, CACHE_TTL_S)


async def search(query: str) -> list[SearchResult]:
    key = " ".join(query.lower().split())
    cached = _cache.get(key)
    if cached is not None:
        return cached

    params = {"media": "podcast", "entity": "podcast", "term": key, "limit": RESULT_LIMIT}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(ITUNES_SEARCH_URL, params=params)
        resp.raise_for_status()
        raw = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise SearchError("directory is rate limiting searches, try again in a minute") from exc
        raise SearchError(f"directory returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise SearchError(f"could not reach directory: {exc}") from exc
    except ValueError as exc:
        raise SearchError("directory returned an unreadable response") from exc

    results = normalize_itunes(raw)
    _cache.put(key, results)
    return results
