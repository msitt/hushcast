"""APScheduler jobs: feed polling, failed-episode requeue, disk cleanup."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .. import settings_store
from ..config import get_config
from ..db import session_factory
from ..models import Episode, Feed
from ..notifications import NotificationEvent, notify
from ..rss import fetch as rss_fetch
from . import state
from .worker import worker

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

POLL_JOB_ID = "poll_feeds"
CLEANUP_JOB_ID = "cleanup"

# Consecutive poll failures before we notify. A feed self-heals from one-off
# blips via the next poll, this is for "the source has been broken for hours".
FEED_POLL_FAIL_THRESHOLD = 5


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes, treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def start() -> None:
    async with session_factory()() as session:
        settings = await settings_store.get_all(session)
    # replace_existing: the in-memory jobstore keeps jobs across a shutdown,
    # so a stop/start cycle (tests) would otherwise raise ConflictingIdError.
    scheduler.add_job(
        poll_all_feeds, "interval",
        minutes=max(1, int(settings["poll_interval_minutes"])),
        id=POLL_JOB_ID, next_run_time=datetime.now(UTC) + timedelta(seconds=10),
        replace_existing=True,
    )
    scheduler.add_job(cleanup, "interval", hours=24, id=CLEANUP_JOB_ID, replace_existing=True)
    scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def reschedule_poll(minutes: int) -> None:
    if scheduler.running:
        scheduler.reschedule_job(POLL_JOB_ID, trigger="interval", minutes=max(1, minutes))


async def poll_all_feeds() -> None:
    async with session_factory()() as session:
        feed_ids = (
            await session.execute(select(Feed.id).where(Feed.enabled.is_(True)))
        ).scalars().all()
    for feed_id in feed_ids:
        try:
            await poll_feed(feed_id)
        except Exception:
            log.exception("poll failed for feed %d", feed_id)
    await requeue_failed()


async def upsert_new_episodes(session, feed: Feed, parsed) -> list[int]:
    """Create Episode rows for any items in `parsed` not already known for `feed`.

    Does not commit - caller is responsible for committing the session. Returns
    the ids of newly-created episodes that are eligible for processing (i.e.
    should be passed to worker.enqueue once committed).
    """
    known_guids = {
        g for (g,) in (
            await session.execute(select(Episode.guid).where(Episode.feed_id == feed.id))
        ).all()
    }
    initial_sync = not known_guids

    new_eps = [e for e in parsed.episodes if e.guid not in known_guids]
    to_queue_ids: list[int] = []
    for pe in new_eps:
        if initial_sync:
            # pre-existing episodes are never auto-processed, queue them manually
            status = state.SKIPPED
        else:
            published_after_add = pe.published_at is None or pe.published_at >= _aware(feed.created_at)
            status = state.QUEUED if published_after_add else state.SKIPPED
        episode = Episode(
            feed_id=feed.id,
            guid=pe.guid,
            title=pe.title,
            published_at=pe.published_at,
            description_html=pe.description_html,
            source_enclosure_url=pe.enclosure_url,
            source_enclosure_type=pe.enclosure_type,
            source_enclosure_length=pe.enclosure_length,
            duration_s=pe.duration_s,
            status=status,
        )
        session.add(episode)
        await session.flush()
        if status == state.QUEUED:
            to_queue_ids.append(episode.id)
    if new_eps:
        log.info("feed %d: %d new episode(s), %d queued", feed.id, len(new_eps), len(to_queue_ids))
    return to_queue_ids


async def poll_feed(feed_id: int) -> None:
    """Fetch one source feed and upsert its episodes, queue what's eligible."""
    factory = session_factory()
    async with factory() as session:
        feed = await session.get(Feed, feed_id)
        if feed is None or not feed.enabled:
            return
        etag, last_modified = feed.etag, feed.last_modified
        source_url = feed.source_url

    try:
        parsed = await rss_fetch.fetch(source_url, etag=etag, last_modified=last_modified)
    except Exception as exc:
        async with factory() as session:
            feed = await session.get(Feed, feed_id)
            if feed is not None:
                feed.poll_error = str(exc)[:2000]
                feed.last_polled_at = datetime.now(UTC)
                feed.consecutive_poll_failures += 1
                title = feed.title or feed.source_url
                fail_count = feed.consecutive_poll_failures
                await session.commit()
                if fail_count == FEED_POLL_FAIL_THRESHOLD:
                    settings = await settings_store.get_all(session)
                    notify(
                        NotificationEvent.FEED_POLL_FAILING,
                        "hushcast: feed polling is failing",
                        f'"{title}" has failed to poll {fail_count} times in a row: {str(exc)[:500]}',
                        settings,
                    )
        raise

    async with factory() as session:
        feed = await session.get(Feed, feed_id)
        if feed is None:
            return
        feed.last_polled_at = datetime.now(UTC)
        feed.poll_error = None
        feed.consecutive_poll_failures = 0
        if parsed.not_modified:
            await session.commit()
            return

        feed.etag = parsed.etag
        feed.last_modified = parsed.last_modified
        if parsed.title:
            feed.title = parsed.title
        feed.description = parsed.description or feed.description
        feed.image_url = parsed.image_url or feed.image_url
        feed.author = parsed.author or feed.author
        feed.link = parsed.link or feed.link

        to_queue_ids = await upsert_new_episodes(session, feed, parsed)
        await session.commit()

    for eid in to_queue_ids:
        worker.enqueue(eid)


async def requeue_failed() -> None:
    async with session_factory()() as session:
        settings = await settings_store.get_all(session)
        max_retries = int(settings["max_episode_retries"])
        rows = (
            await session.execute(
                select(Episode).where(
                    Episode.status == state.FAILED, Episode.retry_count < max_retries
                )
            )
        ).scalars().all()
        for ep in rows:
            ep.status = state.QUEUED
        await session.commit()
    for ep in rows:
        worker.enqueue(ep.id)
    if rows:
        log.info("auto-requeued %d failed episode(s)", len(rows))


async def cleanup() -> None:
    config = get_config()
    factory = session_factory()
    async with factory() as session:
        settings = await settings_store.get_all(session)

    # expire beyond per-feed retention, by count and by age since processing
    max_kept = int(settings["max_kept_episodes"])
    max_days = int(settings["max_kept_days"])
    if max_kept > 0 or max_days > 0:
        age_cutoff = datetime.now(UTC) - timedelta(days=max_days) if max_days > 0 else None
        async with factory() as session:
            feed_ids = (await session.execute(select(Feed.id))).scalars().all()
            for feed_id in feed_ids:
                processed = (
                    await session.execute(
                        select(Episode)
                        .where(Episode.feed_id == feed_id, Episode.status == state.PROCESSED)
                        .order_by(Episode.published_at.desc())
                    )
                ).scalars().all()
                for i, ep in enumerate(processed):
                    over_count = max_kept > 0 and i >= max_kept
                    processed_at = _aware(ep.processed_at)
                    over_age = age_cutoff is not None and processed_at is not None and processed_at < age_cutoff
                    if not over_count and not over_age:
                        continue
                    ep.status = state.EXPIRED
                    if ep.processed_path:
                        Path(ep.processed_path).unlink(missing_ok=True)
                        ep.processed_path = None
            await session.commit()

    # age out kept originals. Scoped to episodes done with the pipeline (won't
    # be auto-retried or manually retried into needing the file again) - a
    # queued/active/failed episode's original stays untouched no matter its
    # age, since re-downloading it isn't guaranteed to work later (the source
    # URL can rotate or die independently of the episode still being in the
    # feed) and losing it would be a permanent, unrecoverable loss.
    if settings["keep_originals"]:
        cutoff = datetime.now(UTC).timestamp() - int(settings["keep_originals_days"]) * 86400
        async with factory() as session:
            rows = (
                await session.execute(
                    select(Episode).where(
                        Episode.original_path.is_not(None),
                        Episode.status.in_((state.PROCESSED, state.EXPIRED, state.SKIPPED)),
                    )
                )
            ).scalars().all()
            for ep in rows:
                p = Path(ep.original_path)
                if not p.exists() or p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    ep.original_path = None
            await session.commit()

    # orphan sweep: files with no live episode row
    async with factory() as session:
        live_ids = {
            str(i) for (i,) in (
                await session.execute(
                    select(Episode.id).where(Episode.status.notin_({state.EXPIRED}))
                )
            ).all()
        }
    for directory in (config.original_audio_dir, config.processed_audio_dir, config.transcripts_dir, config.cues_dir):
        for f in directory.iterdir():
            if f.is_file() and f.name.split(".")[0].isdigit() and f.name.split(".")[0] not in live_ids:
                f.unlink(missing_ok=True)
    log.info("cleanup completed")
