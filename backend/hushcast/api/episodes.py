"""Episode listing, detail, and processing actions."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..models import Episode, Job, LlmCall, Segment, utcnow
from ..pipeline import state
from ..pipeline.worker import worker
from .deps import get_session

router = APIRouter(prefix="/api", tags=["episodes"])

REPROCESS_STEPS = ["download", "transcribe", "detect", "cut"]


def _original_file(ep: Episode) -> Path | None:
    """The kept original audio, if it is still on disk."""
    if not ep.original_path:
        return None
    p = Path(ep.original_path)
    return p if p.exists() else None


class EpisodeOut(BaseModel):
    id: int
    feed_id: int
    guid: str
    title: str
    published_at: datetime | None
    status: str
    status_detail: str | None
    retry_count: int
    duration_s: float | None
    processed_duration_s: float | None
    processed_bytes: int | None
    ad_seconds_removed: float | None
    updated_at: datetime


class SegmentOut(BaseModel):
    id: int
    start_s: float
    end_s: float
    category: str
    confidence: float
    reason: str
    kept: bool
    source: str
    corrected_at: datetime | None


class JobOut(BaseModel):
    id: int
    step: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    log_text: str
    metrics: dict


class EpisodeDetail(EpisodeOut):
    description_html: str
    source_enclosure_url: str
    segments: list[SegmentOut]
    jobs: list[JobOut]
    has_transcript: bool
    has_raw_transcript: bool
    has_cues: bool
    has_original: bool


@router.get("/feeds/{feed_id}/episodes")
async def list_episodes(
    feed_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    q = select(Episode).where(Episode.feed_id == feed_id)
    count_q = select(func.count()).where(Episode.feed_id == feed_id)
    if status:
        q = q.where(Episode.status == status)
        count_q = count_q.where(Episode.status == status)
    total = (await session.execute(count_q)).scalar_one()
    rows = (
        await session.execute(
            q.order_by(Episode.published_at.desc().nulls_last())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [EpisodeOut.model_validate(e, from_attributes=True) for e in rows],
    }


@router.get("/episodes/{episode_id}")
async def episode_detail(episode_id: int, session: AsyncSession = Depends(get_session)) -> EpisodeDetail:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    segments = (
        await session.execute(
            select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.start_s)
        )
    ).scalars().all()
    jobs = (
        await session.execute(
            select(Job).where(Job.episode_id == episode_id).order_by(Job.id)
        )
    ).scalars().all()
    detail = EpisodeDetail(
        **EpisodeOut.model_validate(ep, from_attributes=True).model_dump(),
        description_html=ep.description_html,
        source_enclosure_url=ep.source_enclosure_url,
        segments=[SegmentOut.model_validate(s, from_attributes=True) for s in segments],
        jobs=[
            JobOut(
                id=j.id, step=j.step, status=j.status, started_at=j.started_at,
                finished_at=j.finished_at, error=j.error, log_text=j.log_text,
                metrics=json.loads(j.metrics_json or "{}"),
            )
            for j in jobs
        ],
        has_transcript=(get_config().transcripts_dir / f"{episode_id}.json").exists(),
        has_raw_transcript=(get_config().transcripts_dir / f"{episode_id}.raw.json").exists(),
        has_cues=(get_config().cues_dir / f"{episode_id}.json").exists(),
        has_original=_original_file(ep) is not None,
    )
    return detail


def _load_transcript_for(episode_id: int):
    from ..transcription.base import Transcript

    path = get_config().transcripts_dir / f"{episode_id}.json"
    if not path.exists():
        return None
    try:
        return Transcript.from_json_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError):
        return None


def _snapshot_excerpt(episode_id: int, start_s: float, end_s: float) -> str | None:
    from ..detection.corrections import build_excerpt

    transcript = _load_transcript_for(episode_id)
    if transcript is None:
        return None
    return build_excerpt(transcript, start_s, end_s)


class SegmentPatch(BaseModel):
    kept: bool


class SegmentCreate(BaseModel):
    start_s: float
    end_s: float
    category: str = "ad"
    # True = "this sub-range of a detected block is NOT an ad" (boundary trim,
    # a false-positive correction). False = "this range is a missed ad".
    not_ad: bool = False


@router.patch("/segments/{segment_id}")
async def patch_segment(
    segment_id: int, body: SegmentPatch, session: AsyncSession = Depends(get_session)
) -> SegmentOut:
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(404, "segment not found")
    if seg.source != "llm":
        raise HTTPException(409, "manual segments are removed with DELETE, not toggled")
    seg.kept = body.kept
    if body.kept:
        # false-positive correction: snapshot context now so it survives cleanup
        seg.corrected_at = utcnow()
        if not seg.excerpt:
            seg.excerpt = _snapshot_excerpt(seg.episode_id, seg.start_s, seg.end_s)
    else:
        seg.corrected_at = None  # undo: no longer a correction
    await session.commit()
    return SegmentOut.model_validate(seg, from_attributes=True)


@router.post("/episodes/{episode_id}/segments", status_code=201)
async def add_manual_segment(
    episode_id: int, body: SegmentCreate, session: AsyncSession = Depends(get_session)
) -> SegmentOut:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    if body.end_s <= body.start_s:
        raise HTTPException(400, "end must be after start")

    overlapping = (
        await session.execute(
            select(Segment).where(
                Segment.episode_id == episode_id,
                Segment.kept.is_(False),
                Segment.start_s < body.end_s,
                Segment.end_s > body.start_s,
            )
        )
    ).scalars().all()

    if body.not_ad:
        # Boundary trim: only meaningful inside a detected block.
        if not overlapping:
            raise HTTPException(409, "this range doesn't overlap any detected segment")
        category = overlapping[0].category
        seg = Segment(
            episode_id=episode_id,
            start_s=body.start_s,
            end_s=body.end_s,
            category=category,
            confidence=1.0,
            reason="marked not-an-ad within a detected block",
            source="manual",
            kept=True,
            corrected_at=utcnow(),
            excerpt=_snapshot_excerpt(episode_id, body.start_s, body.end_s),
        )
        session.add(seg)
        await session.commit()
        return SegmentOut.model_validate(seg, from_attributes=True)

    # A range fully inside an active detected segment isn't a missed ad. Recording
    # it as a false negative would feed the distiller a lie. Partial overlap is
    # allowed (it corrects the detector's boundaries).
    if any(s.start_s <= body.start_s and s.end_s >= body.end_s for s in overlapping):
        raise HTTPException(409, "this range is already flagged as an ad")
    seg = Segment(
        episode_id=episode_id,
        start_s=body.start_s,
        end_s=body.end_s,
        category=body.category if body.category in ("ad", "sponsor", "self_promo") else "ad",
        confidence=1.0,
        reason="added manually",
        source="manual",
        corrected_at=utcnow(),
        excerpt=_snapshot_excerpt(episode_id, body.start_s, body.end_s),
    )
    session.add(seg)
    await session.commit()
    return SegmentOut.model_validate(seg, from_attributes=True)


@router.delete("/segments/{segment_id}", status_code=204)
async def delete_segment(segment_id: int, session: AsyncSession = Depends(get_session)) -> None:
    seg = await session.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(404, "segment not found")
    if seg.source != "manual":
        raise HTTPException(409, "only manually added segments can be deleted, use kept to override detected ones")
    await session.delete(seg)
    await session.commit()


class LlmCallSummary(BaseModel):
    id: int
    created_at: datetime
    url: str
    model: str
    provider: str | None
    status_code: int | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    elapsed_s: float
    request_chars: int
    response_chars: int
    event_count: int


def _call_extras(call: LlmCall) -> tuple[str | None, int | None]:
    """(provider, reasoning_tokens) extracted from the raw response payload."""
    if not call.response_json:
        return None, None
    try:
        payload = json.loads(call.response_json)
        details = (payload.get("usage") or {}).get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens")
        return payload.get("provider"), int(reasoning) if reasoning is not None else None
    except (ValueError, TypeError):
        return None, None


@router.get("/episodes/{episode_id}/llm-calls")
async def episode_llm_calls(
    episode_id: int, session: AsyncSession = Depends(get_session)
) -> list[LlmCallSummary]:
    calls = (
        await session.execute(
            select(LlmCall).where(LlmCall.episode_id == episode_id).order_by(LlmCall.id)
        )
    ).scalars().all()
    out = []
    for c in calls:
        provider, reasoning_tokens = _call_extras(c)
        out.append(
            LlmCallSummary(
                id=c.id, created_at=c.created_at, url=c.url, model=c.model,
                provider=provider, status_code=c.status_code, finish_reason=c.finish_reason,
                prompt_tokens=c.prompt_tokens, completion_tokens=c.completion_tokens,
                reasoning_tokens=reasoning_tokens,
                elapsed_s=c.elapsed_s, request_chars=len(c.request_json), response_chars=len(c.response_text),
                event_count=len(json.loads(c.events_json)) if c.events_json else 0,
            )
        )
    return out


@router.get("/llm-calls/{call_id}")
async def llm_call_detail(call_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    call = await session.get(LlmCall, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    provider, reasoning_tokens = _call_extras(call)
    response_raw = json.loads(call.response_json) if call.response_json else None
    reasoning = None
    if isinstance(response_raw, dict):
        try:
            reasoning = response_raw["choices"][0]["message"].get("reasoning")
        except (KeyError, IndexError, TypeError):
            reasoning = None
    return {
        "id": call.id,
        "created_at": call.created_at,
        "url": call.url,
        "model": call.model,
        "provider": provider,
        "status_code": call.status_code,
        "finish_reason": call.finish_reason,
        "messages": json.loads(call.request_json),
        "params": json.loads(call.params_json) if call.params_json else None,
        "response": call.response_text,
        "response_raw": response_raw,
        "reasoning": reasoning if isinstance(reasoning, str) and reasoning.strip() else None,
        "events": json.loads(call.events_json) if call.events_json else [],
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "elapsed_s": call.elapsed_s,
    }


@router.get("/episodes/{episode_id}/download")
async def download_episode(episode_id: int, session: AsyncSession = Depends(get_session)) -> FileResponse:
    ep = await session.get(Episode, episode_id)
    if ep is None or ep.status != state.PROCESSED or not ep.processed_path:
        raise HTTPException(404, "no processed audio for this episode")
    path = Path(ep.processed_path)
    if not path.exists():
        raise HTTPException(404, "processed file missing on disk")
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", ep.title).strip() or f"episode-{episode_id}"
    return FileResponse(path, media_type="audio/mpeg", filename=f"{safe_title}.mp3")


ORIGINAL_MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".wav": "audio/wav",
}


@router.get("/episodes/{episode_id}/download-original")
async def download_original(episode_id: int, session: AsyncSession = Depends(get_session)) -> FileResponse:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    path = _original_file(ep)
    if path is None:
        raise HTTPException(404, "no original audio for this episode")
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", ep.title).strip() or f"episode-{episode_id}"
    media_type = ORIGINAL_MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=f"{safe_title} (original){path.suffix}")


@router.get("/episodes/{episode_id}/transcript")
async def episode_transcript(episode_id: int) -> dict:
    path = get_config().transcripts_dir / f"{episode_id}.json"
    if not path.exists():
        raise HTTPException(404, "no transcript for this episode")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/episodes/{episode_id}/transcript/raw")
async def episode_raw_transcript(episode_id: int) -> dict:
    """Per-call raw transcription provider responses (debug view)."""
    path = get_config().transcripts_dir / f"{episode_id}.raw.json"
    if not path.exists():
        raise HTTPException(404, "no raw transcription log for this episode")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/episodes/{episode_id}/cues")
async def episode_cues(episode_id: int) -> list:
    path = get_config().cues_dir / f"{episode_id}.json"
    if not path.exists():
        raise HTTPException(404, "no cues for this episode")
    return json.loads(path.read_text(encoding="utf-8"))


async def _queue_episode(session: AsyncSession, episode_id: int, allowed_from: set[str]) -> None:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    if ep.status not in allowed_from:
        raise HTTPException(409, f"episode is {ep.status}, cannot queue from that state")
    state.validate_transition(ep.status, state.QUEUED)
    ep.status = state.QUEUED
    ep.status_detail = None
    await session.commit()
    worker.enqueue(episode_id)


@router.post("/episodes/{episode_id}/process")
async def process_episode(episode_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    await _queue_episode(session, episode_id, {state.SKIPPED, state.DISCOVERED, state.FAILED, state.EXPIRED})
    return {"ok": True}


@router.post("/episodes/{episode_id}/retry")
async def retry_episode(episode_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    await _queue_episode(session, episode_id, {state.FAILED})
    return {"ok": True}


@router.post("/episodes/{episode_id}/dismiss")
async def dismiss_episode(episode_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Give up on a failed episode: move it to skipped so it stops alerting.

    status_detail and job history are kept so the failure stays inspectable.
    The episode can be re-queued later via Process (skipped -> queued).
    """
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    if ep.status != state.FAILED:
        raise HTTPException(409, f"episode is {ep.status}, only failed episodes can be dismissed")
    state.validate_transition(ep.status, state.SKIPPED)
    ep.status = state.SKIPPED
    await session.commit()
    return {"ok": True}


@router.post("/episodes/dismiss-failed")
async def dismiss_failed_episodes(
    feed_id: int | None = Query(None), session: AsyncSession = Depends(get_session)
) -> dict:
    """Bulk-dismiss failed episodes (optionally scoped to one feed) to skipped."""
    q = select(Episode).where(Episode.status == state.FAILED)
    if feed_id is not None:
        q = q.where(Episode.feed_id == feed_id)
    episodes = (await session.execute(q)).scalars().all()
    for ep in episodes:
        state.validate_transition(ep.status, state.SKIPPED)
        ep.status = state.SKIPPED
    await session.commit()
    return {"dismissed": len(episodes)}


@router.post("/episodes/{episode_id}/reprocess")
async def reprocess_episode(
    episode_id: int,
    from_step: str = Query("detect", enum=REPROCESS_STEPS),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    if ep.status in state.ACTIVE or ep.status == state.QUEUED:
        raise HTTPException(409, "episode is currently queued/processing")

    config = get_config()
    step_idx = REPROCESS_STEPS.index(from_step)
    # clear artifacts for from_step and everything after it
    if step_idx <= REPROCESS_STEPS.index("cut") and ep.processed_path:
        Path(ep.processed_path).unlink(missing_ok=True)
        ep.processed_path = None
        ep.processed_bytes = None
        ep.processed_duration_s = None
    if step_idx <= REPROCESS_STEPS.index("detect"):
        await session.execute(delete(Segment).where(Segment.episode_id == episode_id))
    if step_idx <= REPROCESS_STEPS.index("transcribe"):
        # the raw provider-call log ({id}.raw.json) is kept on purpose: like llm_calls,
        # it accumulates across runs so earlier attempts stay inspectable in the UI
        (config.transcripts_dir / f"{episode_id}.json").unlink(missing_ok=True)
        # cues are recomputed alongside transcription (the cues step follows it)
        (config.cues_dir / f"{episode_id}.json").unlink(missing_ok=True)
    if step_idx == 0:
        for f in config.original_audio_dir.glob(f"{episode_id}.*"):
            f.unlink(missing_ok=True)
        ep.original_path = None

    ep.status = state.QUEUED
    ep.status_detail = None
    ep.retry_count = 0
    ep.last_failed_step = None
    await session.commit()
    worker.enqueue(episode_id)
    return {"ok": True}
