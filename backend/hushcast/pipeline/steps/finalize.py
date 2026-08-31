"""Probe the processed file, persist final episode fields, clean up originals."""
from __future__ import annotations

from ...audio import ffmpeg
from ...db import session_factory
from ...models import Episode
from ..context import EpisodeContext
from .download import find_existing


async def run(ctx: EpisodeContext) -> None:
    processed = ctx.processed_path
    if not processed.exists():
        raise RuntimeError("processed file missing at finalize")

    duration = await ffmpeg.probe_duration(processed)
    size = processed.stat().st_size

    async with session_factory()() as session:
        episode = await session.get(Episode, ctx.episode_id)
        assert episode is not None
        episode.processed_path = str(processed)
        episode.processed_bytes = size
        episode.processed_duration_s = duration
        episode.ad_seconds_removed = float(ctx.metrics.get("ad_seconds_removed", 0.0))
        await session.commit()

    ctx.log.append(f"finalized: {size} bytes, {duration:.0f}s")

    if not ctx.settings["keep_originals"]:
        original = ctx.original_path or find_existing(ctx)
        if original is not None:
            original.unlink(missing_ok=True)
            ctx.log.append(f"deleted original {original.name}")
        upload = ctx.config.original_audio_dir / f"{ctx.episode_id}.upload.ogg"
        upload.unlink(missing_ok=True)
        async with session_factory()() as session:
            episode = await session.get(Episode, ctx.episode_id)
            if episode is not None:
                episode.original_path = None
                await session.commit()
