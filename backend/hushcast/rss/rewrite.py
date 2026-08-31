"""Build the served RSS feed from DB fields (never round-trips source XML)."""
from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from ..models import Episode, Feed

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _rfc2822(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return format_datetime(dt)


def build_feed_xml(
    feed: Feed,
    episodes: list[Episode],
    *,
    public_url: str,
    token: str,
) -> bytes:
    """Render RSS 2.0 + itunes tags. `episodes` must be processed episodes only."""
    ET.register_namespace("itunes", ITUNES_NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    def sub(parent, tag, text=None, **attrib):
        el = ET.SubElement(parent, tag, attrib)
        if text is not None:
            el.text = text
        return el

    base = public_url.rstrip("/")
    sub(channel, "title", f"[hushcast] {feed.title}")
    sub(channel, "link", feed.link or feed.source_url)
    sub(channel, "description", feed.description or "")
    sub(channel, "language", "en")
    sub(channel, "generator", "hushcast")
    if feed.author:
        sub(channel, f"{{{ITUNES_NS}}}author", feed.author)
    if feed.image_url:
        sub(channel, f"{{{ITUNES_NS}}}image", href=feed.image_url)
        image = sub(channel, "image")
        sub(image, "url", feed.image_url)
        sub(image, "title", f"[hushcast] {feed.title}")
        sub(image, "link", feed.link or feed.source_url)

    for ep in episodes:
        item = sub(channel, "item")
        sub(item, "title", ep.title)
        # Original GUID verbatim: keeps client listening history stable.
        sub(item, "guid", ep.guid, isPermaLink="false")
        pub = _rfc2822(ep.published_at)
        if pub:
            sub(item, "pubDate", pub)
        if ep.description_html:
            sub(item, "description", ep.description_html)
        sub(
            item,
            "enclosure",
            url=f"{base}/p/{token}/audio/{ep.id}.mp3",
            length=str(ep.processed_bytes or 0),
            type="audio/mpeg",
        )
        dur = _fmt_duration(ep.processed_duration_s)
        if dur:
            sub(item, f"{{{ITUNES_NS}}}duration", dur)

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
