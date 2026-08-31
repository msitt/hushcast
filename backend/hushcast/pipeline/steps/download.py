"""Download the source enclosure to original storage (idempotent, atomic)."""
from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

from ...audio import ffmpeg
from ...db import session_factory
from ...models import Episode
from ...rss.fetch import USER_AGENT
from ..context import EpisodeContext

MIN_PLAUSIBLE_BYTES = 100_000  # anything smaller is an error page, not an episode

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


async def run(ctx: EpisodeContext) -> None:
    existing = find_existing(ctx)
    if existing:
        ctx.original_path = existing
        await _persist_original(ctx, existing)
        ctx.log.append(f"download skipped: {existing.name} already present ({existing.stat().st_size} bytes)")
        return

    url = ctx.source_enclosure_url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True
    ) as client:
        async with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
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
    ctx.original_path = dest
    await _persist_original(ctx, dest)
    ctx.metrics["download_bytes"] = size
    ctx.log.append(f"downloaded {size} bytes ({ctx.duration_s:.0f}s) -> {dest.name}")
