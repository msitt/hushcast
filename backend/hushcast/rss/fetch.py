"""Source feed fetching/parsing: httpx conditional GET + feedparser."""
from __future__ import annotations

import calendar
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"


@dataclass
class ParsedEpisode:
    guid: str
    title: str
    published_at: datetime | None
    description_html: str
    enclosure_url: str
    enclosure_type: str
    enclosure_length: int | None
    duration_s: float | None


@dataclass
class ParsedFeed:
    title: str
    description: str
    image_url: str | None
    author: str | None
    link: str | None
    episodes: list[ParsedEpisode] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


def _parse_duration(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        if ":" in value:
            parts = [float(p) for p in value.split(":")]
            secs = 0.0
            for p in parts:
                secs = secs * 60 + p
            return secs
        return float(value)
    except ValueError:
        return None


def parse_bytes(content: bytes) -> ParsedFeed:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"could not parse feed: {parsed.get('bozo_exception')}")

    feed_meta = parsed.feed
    image_url = None
    if feed_meta.get("image") and feed_meta.image.get("href"):
        image_url = feed_meta.image.href
    result = ParsedFeed(
        title=feed_meta.get("title", "").strip(),
        description=feed_meta.get("summary", "") or feed_meta.get("subtitle", ""),
        image_url=image_url,
        author=feed_meta.get("author"),
        link=feed_meta.get("link"),
    )

    for entry in parsed.entries:
        enclosure = None
        for e in entry.get("enclosures", []):
            etype = e.get("type", "") or ""
            if etype.startswith("audio/") or not enclosure:
                enclosure = e
                if etype.startswith("audio/"):
                    break
        if not enclosure or not enclosure.get("href"):
            continue

        length = None
        with contextlib.suppress(TypeError, ValueError):
            length = int(enclosure.get("length") or 0) or None  # length="0" is common, never trust it

        published = None
        if entry.get("published_parsed"):
            published = datetime.fromtimestamp(
                calendar.timegm(entry.published_parsed), tz=UTC
            )

        guid = entry.get("id") or enclosure["href"]
        result.episodes.append(
            ParsedEpisode(
                guid=guid,
                title=entry.get("title", "").strip(),
                published_at=published,
                description_html=entry.get("summary", ""),
                enclosure_url=enclosure["href"],
                enclosure_type=enclosure.get("type") or "audio/mpeg",
                enclosure_length=length,
                duration_s=_parse_duration(entry.get("itunes_duration")),
            )
        )
    return result


async def fetch(
    url: str, *, etag: str | None = None, last_modified: str | None = None
) -> ParsedFeed:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 304:
        return ParsedFeed(title="", description="", image_url=None, author=None, link=None, not_modified=True)
    resp.raise_for_status()
    parsed = parse_bytes(resp.content)
    parsed.etag = resp.headers.get("ETag")
    parsed.last_modified = resp.headers.get("Last-Modified")
    return parsed
