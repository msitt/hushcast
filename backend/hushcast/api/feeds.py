"""Feed CRUD."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..models import Episode, Feed, Segment, utcnow
from ..pipeline import scheduler, state
from ..pipeline.worker import worker
from ..rss import fetch as rss_fetch
from .. import settings_store
from .deps import get_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feeds", tags=["feeds"])

# Hold references to fire-and-forget polls: an un-referenced asyncio task may be
# garbage-collected mid-run, and its exceptions would vanish silently.
_background_polls: set[asyncio.Task] = set()


def _poll_in_background(feed_id: int) -> None:
    task = asyncio.create_task(scheduler.poll_feed(feed_id))
    _background_polls.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_polls.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background poll of feed %d failed: %s", feed_id, t.exception())

    task.add_done_callback(_done)


class FeedCreate(BaseModel):
    url: str
    whitelisted: bool = False
    detection_hints: str | None = None


class FeedPatch(BaseModel):
    enabled: bool | None = None
    whitelisted: bool | None = None
    detection_hints: str | None = None
    learned_hints: str | None = None  # accepting/editing/clearing distilled hints


class FeedOut(BaseModel):
    id: int
    slug: str
    source_url: str
    title: str
    image_url: str | None
    description: str
    enabled: bool
    whitelisted: bool
    detection_hints: str | None
    learned_hints: str | None
    hints_distilled_at: datetime | None
    last_polled_at: datetime | None
    poll_error: str | None
    episode_count: int = 0
    processed_count: int = 0
    failed_count: int = 0
    new_corrections: int = 0
    subscribe_url: str = ""


async def _feed_out(session: AsyncSession, feed: Feed) -> FeedOut:
    total = (
        await session.execute(select(func.count()).where(Episode.feed_id == feed.id))
    ).scalar_one()
    processed = (
        await session.execute(
            select(func.count()).where(
                Episode.feed_id == feed.id, Episode.status == state.PROCESSED
            )
        )
    ).scalar_one()
    failed = (
        await session.execute(
            select(func.count()).where(
                Episode.feed_id == feed.id, Episode.status == state.FAILED
            )
        )
    ).scalar_one()
    corrections_q = (
        select(func.count())
        .select_from(Segment)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.feed_id == feed.id, Segment.corrected_at.is_not(None))
    )
    if feed.hints_distilled_at is not None:
        corrections_q = corrections_q.where(Segment.corrected_at > feed.hints_distilled_at)
    new_corrections = (await session.execute(corrections_q)).scalar_one()

    token = await settings_store.ensure_feed_token(session)
    base = get_config().public_url.rstrip("/")
    out = FeedOut.model_validate(feed, from_attributes=True)
    out.episode_count = total
    out.processed_count = processed
    out.failed_count = failed
    out.new_corrections = new_corrections
    out.subscribe_url = f"{base}/p/{token}/{feed.slug}/feed.xml"
    return out


@router.get("")
async def list_feeds(session: AsyncSession = Depends(get_session)) -> list[FeedOut]:
    feeds = (await session.execute(select(Feed).order_by(Feed.title))).scalars().all()
    return [await _feed_out(session, f) for f in feeds]


@router.post("", status_code=201)
async def create_feed(body: FeedCreate, session: AsyncSession = Depends(get_session)) -> FeedOut:
    existing = (
        await session.execute(select(Feed).where(Feed.source_url == body.url))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "feed already added")

    try:
        parsed = await rss_fetch.fetch(body.url)
    except Exception as exc:
        raise HTTPException(400, f"could not fetch/parse feed: {exc}")
    if not parsed.episodes:
        raise HTTPException(400, "feed has no episodes with audio enclosures")

    base_slug = slugify(parsed.title or "podcast")[:60] or "podcast"
    slug = base_slug
    n = 2
    while (await session.execute(select(Feed).where(Feed.slug == slug))).scalar_one_or_none():
        slug = f"{base_slug}-{n}"
        n += 1

    feed = Feed(
        slug=slug,
        source_url=body.url,
        title=parsed.title or body.url,
        description=parsed.description,
        image_url=parsed.image_url,
        author=parsed.author,
        link=parsed.link,
        whitelisted=body.whitelisted,
        detection_hints=body.detection_hints,
    )
    session.add(feed)
    await session.commit()

    # create the initial episode rows synchronously (reusing the parse above)
    # so they're already present by the time the client navigates to the feed
    feed.last_polled_at = utcnow()
    to_queue_ids = await scheduler.upsert_new_episodes(session, feed, parsed)
    await session.commit()
    for eid in to_queue_ids:
        worker.enqueue(eid)

    return await _feed_out(session, feed)


@router.get("/{feed_id}")
async def get_feed(feed_id: int, session: AsyncSession = Depends(get_session)) -> FeedOut:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed not found")
    return await _feed_out(session, feed)


@router.patch("/{feed_id}")
async def patch_feed(
    feed_id: int, body: FeedPatch, session: AsyncSession = Depends(get_session)
) -> FeedOut:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed not found")
    if body.enabled is not None:
        feed.enabled = body.enabled
    if body.whitelisted is not None:
        feed.whitelisted = body.whitelisted
    if body.detection_hints is not None:
        feed.detection_hints = body.detection_hints or None
    if body.learned_hints is not None:
        feed.learned_hints = body.learned_hints or None
        feed.hints_distilled_at = utcnow()
    await session.commit()
    return await _feed_out(session, feed)


@router.post("/{feed_id}/distill")
async def distill_hints(feed_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Run the hint distiller over this feed's corrections. Returns a PROPOSAL.
    Nothing is saved until the user accepts via PATCH (feed) / settings PUT (global)."""
    from ..detection.corrections import Correction, distill
    from ..detection.llm import LLMClient

    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed not found")

    rows = (
        await session.execute(
            select(Segment, Episode.title)
            .join(Episode, Segment.episode_id == Episode.id)
            .where(Episode.feed_id == feed_id, Segment.corrected_at.is_not(None))
            .order_by(Segment.corrected_at.desc())
        )
    ).all()
    if not rows:
        raise HTTPException(400, "no corrections recorded for this feed yet")

    corrections = [
        Correction(
            # kept=True → human says "not an ad" (false positive, incl. boundary
            # trims). Manual non-kept rows are missed ads (false negatives).
            kind="false_positive" if seg.kept else "false_negative",
            episode_title=title,
            category=seg.category,
            reason=seg.reason,
            excerpt=seg.excerpt,
        )
        for seg, title in rows
    ]
    settings = await settings_store.get_all(session)
    try:
        feed_hints, global_hints = await distill(
            LLMClient(settings),
            podcast_title=feed.title,
            user_hints=feed.detection_hints,
            current_feed_hints=feed.learned_hints,
            current_global_hints=settings["global_learned_hints"] or None,
            corrections=corrections,
        )
    except Exception as exc:
        raise HTTPException(502, f"distillation failed: {exc}")
    return {
        "feed_hints": feed_hints,
        "global_hints": global_hints,
        "corrections_used": len(corrections),
        "current_feed_hints": feed.learned_hints or "",
        "current_global_hints": settings["global_learned_hints"] or "",
    }


@router.post("/{feed_id}/poll")
async def poll_now(feed_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed not found")
    _poll_in_background(feed_id)
    return {"ok": True}


@router.delete("/{feed_id}", status_code=204)
async def delete_feed(feed_id: int, session: AsyncSession = Depends(get_session)) -> None:
    feed = await session.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed not found")
    episodes = (
        await session.execute(select(Episode).where(Episode.feed_id == feed_id))
    ).scalars().all()
    config = get_config()
    for ep in episodes:
        for p in (ep.original_path, ep.processed_path):
            if p:
                Path(p).unlink(missing_ok=True)
        (config.transcripts_dir / f"{ep.id}.json").unlink(missing_ok=True)
        (config.transcripts_dir / f"{ep.id}.raw.json").unlink(missing_ok=True)
        (config.cues_dir / f"{ep.id}.json").unlink(missing_ok=True)
        for f in config.original_audio_dir.glob(f"{ep.id}.*"):
            f.unlink(missing_ok=True)
    await session.delete(feed)
    await session.commit()
