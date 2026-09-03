"""In-process asyncio queue worker: runs pipeline steps for queued episodes."""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select

from .. import settings_store
from ..config import get_config
from ..db import session_factory
from ..errors import RateLimitError
from ..models import Episode, Feed, Job, utcnow
from . import state
from .context import EpisodeContext
from .steps import cues, cut, detect, download, finalize, transcribe

log = logging.getLogger(__name__)

StepFn = Callable[[EpisodeContext], Awaitable[None]]

# (job step name, episode status while running, step fn)
FULL_PIPELINE: list[tuple[str, str, StepFn]] = [
    ("download", state.DOWNLOADING, download.run),
    ("transcribe", state.TRANSCRIBING, transcribe.run),
    # cues reuses TRANSCRIBING: _run_step skips the transition when the status
    # is unchanged (same trick finalize uses with CUTTING)
    ("cues", state.TRANSCRIBING, cues.run),
    ("detect", state.DETECTING, detect.run),
    ("cut", state.CUTTING, cut.run),
    ("finalize", state.CUTTING, finalize.run),
]
WHITELISTED_PIPELINE: list[tuple[str, str, StepFn]] = [
    ("download", state.DOWNLOADING, download.run),
    ("cut", state.CUTTING, cut.run),
    ("finalize", state.CUTTING, finalize.run),
]

TRANSIENT_ERRORS = (httpx.TransportError, httpx.TimeoutException, RateLimitError)
STEP_ATTEMPTS = 3
# Steps whose HTTP timeout is user-configurable: step -> (settings key, where in the UI)
STEP_TIMEOUT_SETTINGS = {
    "transcribe": ("transcription_timeout_s", "Settings → Transcription → Timeout"),
    "detect": ("llm_timeout_s", "Settings → Ad detection (LLM) → Timeout"),
    "cues": ("cue_remote_timeout_s", "Settings → Audio cues → Remote timeout"),
}


def _describe_error(exc: BaseException, step_name: str, settings: dict) -> str:
    """Human-readable error text (httpx timeout exceptions stringify to nothing)."""
    detail = str(exc).strip()
    if isinstance(exc, httpx.TimeoutException):
        msg = "the server did not respond in time"
        hint = STEP_TIMEOUT_SETTINGS.get(step_name)
        if hint:
            key, where = hint
            try:
                timeout_s = float(settings.get(key) or 0)
            except (TypeError, ValueError):
                timeout_s = 0.0
            if timeout_s:
                msg = f"no response from the server within {timeout_s:.0f}s"
            msg += f", consider raising the timeout under {where}"
        return f"timed out: {msg}"
    if isinstance(exc, httpx.ConnectError):
        return f"could not connect to the server ({detail or 'connection refused or unreachable'})"
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
# Cap how long we'll sit idle honoring a provider's Retry-After, so one slow
# provider response can't block the worker loop indefinitely.
MAX_RATE_LIMIT_WAIT_S = 120.0


class Worker:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.pending: set[int] = set()  # ids queued or running (dedup)
        self.active: dict[int, str] = {}  # episode_id -> current step
        self._tasks: list[asyncio.Task] = []
        self._stopping = False

    async def start(self) -> None:
        # Support a full stop/start cycle (tests run several app lifespans in
        # one process): clear the stop flag and rebuild the queue, which binds
        # to the event loop that first uses it.
        self._stopping = False
        self.queue = asyncio.Queue()
        self.pending.clear()
        self.active.clear()
        async with session_factory()() as session:
            settings = await settings_store.get_all(session)
        concurrency = max(1, int(settings["max_concurrent_episodes"]))
        self._tasks = [asyncio.create_task(self._consumer(i)) for i in range(concurrency)]
        await self.resume()
        log.info("worker started with concurrency=%d", concurrency)

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def resume(self) -> None:
        """Re-queue episodes whose in-flight work was lost to a restart."""
        async with session_factory()() as session:
            # Jobs still marked running/pending were interrupted by the restart,
            # close them out so the audit log doesn't show phantom in-flight work.
            orphans = (
                await session.execute(
                    select(Job).where(Job.status.in_(("running", "pending")))
                )
            ).scalars().all()
            for job in orphans:
                job.status = "failed"
                job.error = "interrupted by server restart"
                job.finished_at = utcnow()
            if orphans:
                await session.commit()
                log.info("closed %d job(s) orphaned by restart", len(orphans))
        async with session_factory()() as session:
            rows = (
                await session.execute(
                    select(Episode).where(Episode.status.in_(state.RESUMABLE))
                )
            ).scalars().all()
            for ep in rows:
                if ep.status != state.QUEUED:
                    ep.status = state.QUEUED
            await session.commit()
        for ep in rows:
            self.enqueue(ep.id)
        if rows:
            log.info("resumed %d episode(s) after restart", len(rows))

    def enqueue(self, episode_id: int) -> bool:
        if episode_id in self.pending:
            return False
        self.pending.add(episode_id)
        self.queue.put_nowait(episode_id)
        return True

    async def _consumer(self, n: int) -> None:
        while not self._stopping:
            episode_id = await self.queue.get()
            try:
                await self._process(episode_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unhandled error processing episode %d", episode_id)
            finally:
                self.pending.discard(episode_id)
                self.active.pop(episode_id, None)
                self.queue.task_done()

    async def _process(self, episode_id: int) -> None:
        factory = session_factory()
        async with factory() as session:
            episode = await session.get(Episode, episode_id)
            if episode is None or episode.status != state.QUEUED:
                return
            feed = await session.get(Feed, episode.feed_id)
            settings = await settings_store.get_all(session)
            ctx = EpisodeContext(
                episode_id=episode.id,
                feed_id=episode.feed_id,
                episode_title=episode.title,
                podcast_title=feed.title if feed else "",
                guid=episode.guid,
                source_enclosure_url=episode.source_enclosure_url,
                whitelisted=bool(feed and feed.whitelisted),
                detection_hints=feed.detection_hints if feed else None,
                learned_hints=feed.learned_hints if feed else None,
                config=get_config(),
                settings=settings,
            )

        pipeline = WHITELISTED_PIPELINE if ctx.whitelisted else FULL_PIPELINE
        for step_name, status, fn in pipeline:
            self.active[episode_id] = step_name
            ok = await self._run_step(ctx, step_name, status, fn)
            if not ok:
                return
            # carry duration forward for later steps
            async with factory() as session:
                episode = await session.get(Episode, episode_id)
                if ctx.duration_s and episode and not episode.duration_s:
                    episode.duration_s = ctx.duration_s
                    await session.commit()

        async with factory() as session:
            episode = await session.get(Episode, episode_id)
            if episode is not None:
                state.validate_transition(episode.status, state.PROCESSED)
                episode.status = state.PROCESSED
                episode.status_detail = None
                episode.retry_count = 0
                episode.last_failed_step = None
                await session.commit()
        log.info("episode %d processed", episode_id)

    async def _run_step(
        self, ctx: EpisodeContext, step_name: str, status: str, fn: StepFn
    ) -> bool:
        factory = session_factory()
        async with factory() as session:
            episode = await session.get(Episode, ctx.episode_id)
            if episode is None:
                return False
            if episode.status != status:
                state.validate_transition(episode.status, status)
                episode.status = status
            job = Job(episode_id=ctx.episode_id, step=step_name, status="running", started_at=utcnow())
            session.add(job)
            await session.commit()
            job_id = job.id

        log_start = len(ctx.log)
        error: str | None = None
        for attempt in range(1, STEP_ATTEMPTS + 1):
            try:
                await fn(ctx)
                error = None
                break
            except TRANSIENT_ERRORS as exc:
                error = _describe_error(exc, step_name, ctx.settings)
                ctx.log.append(f"attempt {attempt}/{STEP_ATTEMPTS} failed: {error}")
                if attempt == STEP_ATTEMPTS:
                    ctx.log.append(f"giving up on {step_name} after {STEP_ATTEMPTS} attempts")
                if attempt < STEP_ATTEMPTS:
                    delay = 5 * attempt
                    if isinstance(exc, RateLimitError) and exc.retry_after:
                        delay = min(max(delay, exc.retry_after), MAX_RATE_LIMIT_WAIT_S)
                        ctx.log.append(f"provider asked us to wait {exc.retry_after:.0f}s, sleeping {delay:.0f}s")
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = _describe_error(exc, step_name, ctx.settings)
                ctx.log.append(f"step failed: {error}")
                log.debug("step %s traceback:\n%s", step_name, traceback.format_exc())
                break

        async with factory() as session:
            job = await session.get(Job, job_id)
            episode = await session.get(Episode, ctx.episode_id)
            job.finished_at = utcnow()
            job.log_text = "\n".join(ctx.log[log_start:])[:20000]
            job.metrics_json = json.dumps(ctx.metrics)
            if error is None:
                job.status = "success"
            else:
                job.status = "failed"
                job.error = error[:2000]
                if episode is not None:
                    episode.status = state.FAILED
                    episode.status_detail = f"{step_name}: {error}"[:2000]
                    # Budget is per-step: a step failing again extends the streak,
                    # a different step failing starts a fresh one.
                    if episode.last_failed_step == step_name:
                        episode.retry_count += 1
                    else:
                        episode.last_failed_step = step_name
                        episode.retry_count = 1
            await session.commit()
        return error is None


worker = Worker()
