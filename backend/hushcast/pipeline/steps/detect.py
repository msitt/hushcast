"""LLM ad detection + post-processing, persists Segment rows."""
from __future__ import annotations

import time

from sqlalchemy import delete, select

import json

from ...db import session_factory
from ...detection import llm as llm_mod
from ...detection import prompts, refine, segments as seg
from ...models import LlmCall, Segment
from ..context import EpisodeContext
from .cues import load_cues
from .transcribe import load_transcript


async def run(ctx: EpisodeContext) -> None:
    factory = session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(Segment).where(Segment.episode_id == ctx.episode_id))
        ).scalars().all()
    if existing:
        ctx.log.append(f"detect skipped: {len(existing)} segment(s) already stored")
        return

    transcript = load_transcript(ctx)
    if transcript is None:
        raise RuntimeError("no transcript on disk, cannot detect")
    ctx.duration_s = ctx.duration_s or transcript.duration

    cues = load_cues(ctx)
    if cues:
        ctx.log.append(f"loaded {len(cues)} audio cue(s)")
    prompt_cues = cues if (cues and bool(ctx.settings["cue_prompt_annotations"])) else None

    async def record_call(call: dict) -> None:
        async with factory() as session:
            session.add(
                LlmCall(
                    episode_id=ctx.episode_id,
                    url=call["url"],
                    model=call["model"],
                    request_json=json.dumps(call["messages"]),
                    response_text=call["response"],
                    params_json=json.dumps(call["params"]),
                    response_json=json.dumps(call["response_json"]) if call["response_json"] is not None else None,
                    events_json=json.dumps(call["events"]),
                    status_code=call["status_code"],
                    finish_reason=call["finish_reason"],
                    prompt_tokens=call["prompt_tokens"],
                    completion_tokens=call["completion_tokens"],
                    elapsed_s=call["elapsed_s"],
                )
            )
            await session.commit()

    client = llm_mod.LLMClient(ctx.settings)
    started = time.monotonic()
    raw = await llm_mod.detect_ads(
        client,
        transcript,
        system_prompt=ctx.settings["detection_prompt"],
        podcast_title=ctx.podcast_title,
        episode_title=ctx.episode_title,
        detection_hints=ctx.detection_hints,
        learned_hints=ctx.learned_hints,
        global_learned_hints=ctx.settings["global_learned_hints"] or None,
        context_budget_tokens=int(ctx.settings["llm_context_budget_tokens"]),
        cues=prompt_cues,
        cue_min_prompt_duration_s=float(ctx.settings["cue_min_prompt_duration_s"]),
        log_lines=ctx.log,
        recorder=record_call,
    )
    elapsed = time.monotonic() - started

    # Post-process against the real audio duration (ffprobe, via download), not
    # transcript.duration: the latter ends at the last word, so when speech runs
    # to the end of the episode the tail gap refinement needs at the edge would
    # never exist and an outro ad's cut gets dragged inward (word_gaps docstring).
    duration = ctx.duration_s or transcript.duration

    # boundary refinement is a no-op when the transcript has no word timestamps
    refine_gaps = refine.word_gaps(refine.collect_words(transcript), duration) or None

    final = seg.postprocess(
        raw,
        duration=duration,
        boundaries=prompts.boundaries(transcript),
        snap_tolerance_s=float(ctx.settings["snap_tolerance_s"]),
        min_confidence=float(ctx.settings["min_confidence"]),
        min_duration_s=float(ctx.settings["min_duration_s"]),
        merge_gap_s=float(ctx.settings["merge_gap_s"]),
        cue_intervals=[(c.start, c.end) for c in cues] if cues else None,
        bridge_max_gap_s=float(ctx.settings["cue_bridge_max_gap_s"]),
        edge_max_extension_s=float(ctx.settings["cue_edge_max_extension_s"]),
        refine_gaps=refine_gaps,
        refine_window_s=float(ctx.settings["refine_window_s"]),
        refine_min_gap_s=float(ctx.settings["refine_min_gap_s"]),
    )

    async with factory() as session:
        await session.execute(delete(Segment).where(Segment.episode_id == ctx.episode_id))
        for s in final:
            session.add(
                Segment(
                    episode_id=ctx.episode_id,
                    start_s=s.start,
                    end_s=s.end,
                    category=s.category,
                    confidence=s.confidence,
                    reason=s.reason,
                )
            )
        await session.commit()

    removed = sum(s.duration for s in final)
    ctx.metrics["detect_seconds"] = round(elapsed, 1)
    ctx.metrics["segments_raw"] = len(raw)
    ctx.metrics["segments_final"] = len(final)
    ctx.metrics["ad_seconds_detected"] = round(removed, 1)
    ctx.log.append(
        f"detection: {len(raw)} raw -> {len(final)} final segment(s), "
        f"{removed:.0f}s flagged, LLM time {elapsed:.0f}s"
    )
