"""Cut detected segments out of the audio (idempotent: skips when output exists)."""
from __future__ import annotations

import time

from sqlalchemy import select

from ...audio import ffmpeg
from ...db import session_factory
from ...detection.segments import AdSegment, keep_ranges
from ...evals.truth import SegmentRow, effective_truth
from ...models import Segment
from ..context import EpisodeContext
from .download import find_existing


async def run(ctx: EpisodeContext) -> None:
    if ctx.processed_path.exists() and ctx.processed_path.stat().st_size > 0:
        ctx.log.append("cut skipped: processed file already exists")
        return

    if ctx.original_path is None:
        ctx.original_path = find_existing(ctx)
    if ctx.original_path is None:
        raise RuntimeError("original audio missing, cannot cut")

    async with session_factory()() as session:
        rows = (
            await session.execute(
                select(Segment).where(Segment.episode_id == ctx.episode_id)
            )
        ).scalars().all()
    segments = cut_segments(
        [SegmentRow(r.start_s, r.end_s, r.category, r.kept, r.source) for r in rows]
    )

    # Probe the actual file. Transcript duration ends at the last spoken word,
    # which would truncate outro music/silence from the final keep-range.
    duration = await ffmpeg.probe_duration(ctx.original_path)
    mp3_quality = int(ctx.settings["mp3_quality"])
    started = time.monotonic()
    if not segments:
        ctx.log.append("no ad segments to cut, copying through")
        await ffmpeg.copy_to_mp3(ctx.original_path, ctx.processed_path, mp3_quality=mp3_quality)
    else:
        ranges = keep_ranges(segments, duration)
        ctx.log.append(
            f"cutting {len(segments)} segment(s), keeping {len(ranges)} range(s) "
            f"of {duration:.0f}s total"
        )
        await ffmpeg.cut(ctx.original_path, ctx.processed_path, ranges, mp3_quality=mp3_quality)
    ctx.metrics["cut_seconds"] = round(time.monotonic() - started, 1)
    ctx.metrics["ad_seconds_removed"] = round(sum(s.duration for s in segments), 1)


def cut_segments(rows: list[SegmentRow]) -> list[AdSegment]:
    """What to cut: the same spans evals treat as the truth.

    Detected segments not marked kept, plus manually added ads, minus "not an ad"
    trims inside them. Promo-only episodes cut nothing. Optional ranges are
    eval-only, so they are left out and never change what is cut.
    """
    truth = effective_truth([r for r in rows if r.source != "optional"])
    if truth.promo:
        return []
    return [AdSegment(start=s.start, end=s.end, category=s.category) for s in truth.spans]
