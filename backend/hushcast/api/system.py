"""System endpoints: live status, environment info, storage, stats, alerts, logs."""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__, auth, logbuffer, loglevel
from ..config import AppConfig, get_config
from ..models import Episode, Feed, LlmCall, Segment, TranscriptionCall
from ..pipeline import state
from ..pipeline.scheduler import CLEANUP_JOB_ID, POLL_JOB_ID, scheduler
from ..pipeline.worker import worker
from .deps import get_session

router = APIRouter(prefix="/api/system", tags=["system"])

STARTED_AT = datetime.now(UTC)

# Statuses whose episodes count toward lifetime stats: expiry deletes the audio
# file but keeps the row and its duration/ad metrics.
FINISHED = (state.PROCESSED, state.EXPIRED)

LOW_DISK_BYTES = 5 * 1024**3


@lru_cache
def _ffmpeg_version() -> str | None:
    """First-token version from `ffmpeg -version`, or None when not on PATH."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, timeout=10
        ).stdout
        return out.splitlines()[0].removeprefix("ffmpeg version ").split(" ")[0]
    except Exception:
        return "unknown"


def provider_host(url: str) -> str:
    """Compact provider label for an LLM endpoint URL."""
    return urlparse(url).netloc or url


def _next_run(job_id: str) -> str | None:
    if not scheduler.running:
        return None
    job = scheduler.get_job(job_id)
    return job.next_run_time.isoformat() if job and job.next_run_time else None


def _dir_bytes(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


async def _alerts(session: AsyncSession, config: AppConfig) -> list[dict]:
    alerts: list[dict] = []
    if _ffmpeg_version() is None:
        alerts.append(
            {
                "severity": "error",
                "kind": "ffmpeg_missing",
                "message": "ffmpeg not found on PATH, episodes cannot be processed",
                "link": None,
            }
        )
    broken_feeds = (
        await session.execute(
            select(Feed.id, Feed.title, Feed.poll_error).where(Feed.poll_error.is_not(None))
        )
    ).all()
    for feed_id, title, poll_error in broken_feeds:
        alerts.append(
            {
                "severity": "warning",
                "kind": "poll_error",
                "message": f"{title or f'Feed {feed_id}'}: poll failing ({poll_error})",
                "link": f"/podcasts/{feed_id}",
            }
        )
    failed_by_feed = (
        await session.execute(
            select(Episode.feed_id, Feed.title, func.count())
            .join(Feed, Episode.feed_id == Feed.id)
            .where(Episode.status == state.FAILED)
            .group_by(Episode.feed_id)
            .order_by(Feed.title)
        )
    ).all()
    for feed_id, title, n in failed_by_feed:
        alerts.append(
            {
                "severity": "warning",
                "kind": "failed_episodes",
                "message": f"{title or f'Feed {feed_id}'}: {n} episode{'s' if n != 1 else ''} failed processing",
                "link": f"/podcasts/{feed_id}?status=failed",
                "feed_id": feed_id,
            }
        )
    free = shutil.disk_usage(config.data_dir).free
    if free < LOW_DISK_BYTES:
        alerts.append(
            {
                "severity": "error" if free < LOW_DISK_BYTES // 5 else "warning",
                "kind": "low_disk",
                "message": f"Low disk space on data volume: {free / 1024**3:.1f} GB free",
                "link": None,
            }
        )
    return alerts


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> dict:
    counts = {
        s: c
        for s, c in (
            await session.execute(select(Episode.status, func.count()).group_by(Episode.status))
        ).all()
    }
    return {
        "version": __version__,
        "queue_depth": worker.queue.qsize(),
        "active": [{"episode_id": eid, "step": step} for eid, step in worker.active.items()],
        "episode_counts": counts,
        "processing": sum(counts.get(s, 0) for s in state.ACTIVE | {state.QUEUED}),
        "alert_count": len(await _alerts(session, get_config())),
    }


@router.get("/info")
async def info() -> dict:
    config = get_config()
    return {
        "version": __version__,
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "ffmpeg_version": _ffmpeg_version(),
        "started_at": STARTED_AT.isoformat(),
        "config_dir": str(config.config_dir.resolve()),
        "data_dir": str(config.data_dir.resolve()),
        "database_path": str((config.config_dir / "hushcast.db").resolve()),
        "public_url": config.public_url,
        "auth_mode": auth.mode(),
        "log_level": loglevel.current(),
        "log_level_env_override": config.log_level.upper() if config.log_level else None,
        "python_executable": sys.executable,
        "next_poll_at": _next_run(POLL_JOB_ID),
        "next_cleanup_at": _next_run(CLEANUP_JOB_ID),
    }


@router.get("/storage")
async def storage() -> dict:
    config = get_config()
    volumes = []
    for name, path in (("config", config.config_dir), ("data", config.data_dir)):
        usage = shutil.disk_usage(path)
        volumes.append(
            {
                "name": name,
                "path": str(path.resolve()),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            }
        )
    db_bytes = sum(
        f.stat().st_size for f in config.config_dir.glob("hushcast.db*") if f.is_file()
    )
    breakdown = [
        {"name": "Original audio", "bytes": _dir_bytes(config.original_audio_dir)},
        {"name": "Processed audio", "bytes": _dir_bytes(config.processed_audio_dir)},
        {"name": "Transcripts", "bytes": _dir_bytes(config.transcripts_dir)},
        {"name": "Cues", "bytes": _dir_bytes(config.cues_dir)},
        {"name": "Database", "bytes": db_bytes},
    ]
    return {"volumes": volumes, "breakdown": breakdown}


@router.get("/alerts")
async def alerts(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await _alerts(session, get_config())


@router.get("/logs")
async def logs(
    level: str = Query("DEBUG"),
    limit: int = Query(500, ge=1, le=logbuffer.CAPACITY),
) -> dict:
    min_level = logging.getLevelNamesMapping().get(level.upper(), logging.NOTSET)
    records = [
        {k: v for k, v in rec.items() if k != "levelno"}
        for rec in logbuffer.handler.tail(limit=limit, min_level=min_level)
    ]
    return {"records": records, "capacity": logbuffer.handler.capacity}


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)) -> dict:
    finished = Episode.status.in_(FINISHED)

    totals = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Episode.duration_s), 0.0),
                func.coalesce(func.sum(Episode.ad_seconds_removed), 0.0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Episode.source_enclosure_length.is_not(None))
                                & (Episode.processed_bytes.is_not(None)),
                                Episode.source_enclosure_length - Episode.processed_bytes,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(finished)
        )
    ).one()
    episodes_processed, duration_s, ad_seconds, bytes_saved = totals

    # Ad share only over non-whitelisted feeds, whitelisted copy-throughs would
    # drag the average toward zero.
    per_feed = (
        await session.execute(
            select(
                Feed.id,
                Feed.title,
                func.count(),
                func.sum(Episode.duration_s),
                func.sum(Episode.ad_seconds_removed),
            )
            .join(Episode, Episode.feed_id == Feed.id)
            .where(
                finished,
                Feed.whitelisted.is_(False),
                Episode.duration_s.is_not(None),
                Episode.ad_seconds_removed.is_not(None),
            )
            .group_by(Feed.id)
        )
    ).all()
    detected_duration = sum(dur for _fid, _title, _n, dur, _ads in per_feed)
    detected_ads = sum(ads for _fid, _title, _n, _dur, ads in per_feed)
    top_feeds = sorted(
        (
            {
                "feed_id": fid,
                "title": title,
                "episodes": n,
                "duration_s": dur,
                "ad_seconds": ads,
                "ad_pct": ads / dur * 100 if dur else 0.0,
            }
            for fid, title, n, dur, ads in per_feed
            if dur
        ),
        key=lambda f: f["ad_pct"],
        reverse=True,
    )[:5]

    segment_counts = (
        await session.execute(
            select(
                func.coalesce(func.sum(case((Segment.kept.is_(False), 1), else_=0)), 0),
                func.coalesce(func.sum(case((Segment.corrected_at.is_not(None), 1), else_=0)), 0),
            )
            .select_from(Segment)
            .join(Episode, Segment.episode_id == Episode.id)
            .where(finished)
        )
    ).one()
    ad_segments_cut, corrections = segment_counts

    llm_rows = (
        await session.execute(
            select(
                LlmCall.url,
                LlmCall.model,
                func.count(),
                func.coalesce(func.sum(LlmCall.prompt_tokens), 0),
                func.coalesce(func.sum(LlmCall.completion_tokens), 0),
                func.coalesce(func.sum(LlmCall.elapsed_s), 0.0),
            ).group_by(LlmCall.url, LlmCall.model)
        )
    ).all()
    by_model: dict[tuple[str, str], dict] = {}
    for url, model, calls, ptok, ctok, elapsed in llm_rows:
        key = (provider_host(url), model)
        entry = by_model.setdefault(
            key,
            {
                "provider": key[0],
                "model": model,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "elapsed_s": 0.0,
            },
        )
        entry["calls"] += calls
        entry["prompt_tokens"] += ptok
        entry["completion_tokens"] += ctok
        entry["elapsed_s"] += elapsed
    llm_models = sorted(
        by_model.values(), key=lambda m: m["prompt_tokens"] + m["completion_tokens"], reverse=True
    )

    transcription_rows = (
        await session.execute(
            select(
                TranscriptionCall.url,
                TranscriptionCall.model,
                func.count(),
                func.coalesce(func.sum(TranscriptionCall.audio_s), 0.0),
                func.coalesce(func.sum(TranscriptionCall.elapsed_s), 0.0),
            ).group_by(TranscriptionCall.url, TranscriptionCall.model)
        )
    ).all()
    tr_by_model: dict[tuple[str, str], dict] = {}
    for url, model, calls, audio_s, elapsed in transcription_rows:
        key = (provider_host(url), model)
        entry = tr_by_model.setdefault(
            key,
            {
                "provider": key[0],
                "model": model,
                "calls": 0,
                "audio_s": 0.0,
                "elapsed_s": 0.0,
            },
        )
        entry["calls"] += calls
        entry["audio_s"] += audio_s
        entry["elapsed_s"] += elapsed
    transcription_models = sorted(
        tr_by_model.values(), key=lambda m: m["audio_s"], reverse=True
    )

    return {
        "episodes_processed": episodes_processed,
        "duration_processed_s": duration_s,
        "ad_seconds_removed": ad_seconds,
        "ad_pct": detected_ads / detected_duration * 100 if detected_duration else None,
        "ad_segments_cut": ad_segments_cut,
        "corrections": corrections,
        "bytes_saved": bytes_saved,
        "top_feeds": top_feeds,
        "llm": {
            "total": {
                "calls": sum(m["calls"] for m in llm_models),
                "prompt_tokens": sum(m["prompt_tokens"] for m in llm_models),
                "completion_tokens": sum(m["completion_tokens"] for m in llm_models),
                "elapsed_s": sum(m["elapsed_s"] for m in llm_models),
            },
            "by_model": llm_models,
        },
        "transcription": {
            "total": {
                "calls": sum(m["calls"] for m in transcription_models),
                "audio_s": sum(m["audio_s"] for m in transcription_models),
                "elapsed_s": sum(m["elapsed_s"] for m in transcription_models),
            },
            "by_model": transcription_models,
        },
    }
