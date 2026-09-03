"""Download the source enclosure to original storage (idempotent, atomic)."""
from __future__ import annotations

from pathlib import Path

import httpx

from ...audio import ffmpeg
from ...db import session_factory
from ...models import Episode, Feed
from ...rss import fetch as rss_fetch
from ...rss.fetch import USER_AGENT
from ..context import EpisodeContext

MIN_PLAUSIBLE_BYTES = 100_000  # anything smaller is an error page, not an episode

# Statuses that plausibly mean "this exact URL is gone", worth refreshing
# from the source feed and retrying once, rather than a transient server
# hiccup the outer step-retry loop already handles.
STALE_URL_STATUSES = {400, 401, 403, 404, 410, 451}

EXT_BY_MIME = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
    "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/wav": ".wav",
}


def _pick_extension(url: str, content_type: str | None) -> str:
    if content_type:
        ext = EXT_BY_MIME.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    path = httpx.URL(url).path.lower()
    for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".mp4"):
        if path.endswith(ext):
            return ext
    return ".mp3"


def find_existing(ctx: EpisodeContext) -> Path | None:
    for candidate in ctx.config.original_audio_dir.glob(f"{ctx.episode_id}.*"):
        if candidate.stat().st_size >= MIN_PLAUSIBLE_BYTES and not candidate.name.endswith(".part"):
            return candidate
    return None


async def _persist_original(ctx: EpisodeContext, path: Path) -> None:
    # Overwrites the feed's claimed itunes:duration: with dynamic ad insertion
    # the file actually served routinely differs from what the RSS advertises.
    duration = await ffmpeg.probe_duration(path)
    ctx.duration_s = duration
    async with session_factory()() as session:
        episode = await session.get(Episode, ctx.episode_id)
        if episode is not None:
            episode.original_path = str(path)
            episode.duration_s = duration
            await session.commit()


async def _refresh_enclosure_url(ctx: EpisodeContext) -> str | None:
    """Re-poll the source feed for this episode's current enclosure URL.

    Retries always replay `ctx.source_enclosure_url`, which is captured once
    at episode discovery and never otherwise refreshed - so a host that
    rotates or expires media URLs (common with dynamic ad insertion) leaves
    the episode permanently stuck on a dead link. Returns the new URL if the
    episode is still in the feed and its URL has changed, else None (feed
    fetch failed, episode no longer listed, or the URL is unchanged).
    """
    async with session_factory()() as session:
        feed = await session.get(Feed, ctx.feed_id)
    if feed is None:
        return None
    try:
        parsed = await rss_fetch.fetch(feed.source_url)
    except Exception as exc:  # noqa: BLE001 - best effort, fall through to the original error
        ctx.log.append(f"could not re-poll source feed for a fresh URL: {exc}")
        return None
    match = next((e for e in parsed.episodes if e.guid == ctx.guid), None)
    if match is None:
        ctx.log.append("episode is no longer listed in the source feed, cannot refresh URL")
        return None
    if match.enclosure_url == ctx.source_enclosure_url:
        return None
    async with session_factory()() as session:
        episode = await session.get(Episode, ctx.episode_id)
        if episode is not None:
            episode.source_enclosure_url = match.enclosure_url
            await session.commit()
    ctx.log.append("source URL has changed since discovery, refreshed from feed and retrying")
    return match.enclosure_url


async def _fetch(ctx: EpisodeContext, url: str) -> tuple[Path, int]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True
    ) as client, client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
        resp.raise_for_status()
        ext = _pick_extension(str(resp.url), resp.headers.get("Content-Type"))
        dest = ctx.config.original_audio_dir / f"{ctx.episode_id}{ext}"
        part = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with part.open("wb") as f:
            async for chunk in resp.aiter_bytes(1 << 16):
                f.write(chunk)
                size += len(chunk)

    if size < MIN_PLAUSIBLE_BYTES:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file is implausibly small ({size} bytes) from {url}")
    part.replace(dest)
    return dest, size


async def run(ctx: EpisodeContext) -> None:
    existing = find_existing(ctx)
    if existing:
        ctx.original_path = existing
        await _persist_original(ctx, existing)
        ctx.log.append(f"download skipped: {existing.name} already present ({existing.stat().st_size} bytes)")
        return

    try:
        dest, size = await _fetch(ctx, ctx.source_enclosure_url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in STALE_URL_STATUSES:
            raise
        new_url = await _refresh_enclosure_url(ctx)
        if new_url is None:
            raise
        ctx.source_enclosure_url = new_url
        dest, size = await _fetch(ctx, new_url)

    ctx.original_path = dest
    await _persist_original(ctx, dest)
    ctx.metrics["download_bytes"] = size
    ctx.log.append(f"downloaded {size} bytes ({ctx.duration_s:.0f}s) -> {dest.name}")
