"""Compute audio cues (non-speech regions) for the episode (idempotent).

Cue failures never fail the pipeline: a broken remote service falls back to
the built-in silencedetect provider, and a silencedetect failure writes an
empty cue list so detection proceeds exactly as if cues were disabled. When
the provider is off, no file is written. A later reprocess with cues enabled
isn't blocked by idempotency, and detect treats a missing file as "no cues".
"""
from __future__ import annotations

import json
import time

from ...cues import SilenceDetectProvider, build_cue_provider
from ...cues.base import Cue, cues_from_json, cues_to_json
from ..context import EpisodeContext


def load_cues(ctx: EpisodeContext) -> list[Cue] | None:
    if not ctx.cues_path.exists():
        return None
    try:
        return cues_from_json(json.loads(ctx.cues_path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError):
        return None


def _transcript_music(ctx: EpisodeContext, cues: list[Cue]) -> list[Cue]:
    """Music ranges the transcription provider reported, as cues.

    A range that a provider music cue already mostly covers is left out, so the
    prompt does not list the same music twice.
    """
    try:
        data = json.loads(ctx.transcript_path.read_text(encoding="utf-8"))
        ranges = [(float(a), float(b)) for a, b in data.get("music") or []]
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    have = [(c.start, c.end) for c in cues if c.kind == "music"]
    out: list[Cue] = []
    for a, b in ranges:
        covered = sum(max(0.0, min(b, e) - max(a, s)) for s, e in have)
        if b > a and covered < 0.5 * (b - a):
            out.append(Cue(start=a, end=b, kind="music"))
    return out


async def run(ctx: EpisodeContext) -> None:
    provider = build_cue_provider(ctx.settings)
    if provider is None:
        ctx.log.append("cues skipped: cue provider is off")
        return
    if ctx.cues_path.exists():
        ctx.log.append("cues skipped: already on disk")
        return
    assert ctx.original_path is not None

    started = time.monotonic()
    provider_used = str(ctx.settings.get("cue_provider"))
    try:
        cues = await provider.detect(ctx.original_path)
    except Exception as exc:  # never fail the pipeline over cues
        if isinstance(provider, SilenceDetectProvider):
            ctx.log.append(f"silencedetect failed ({type(exc).__name__}: {exc}), continuing without cues")
            cues = []
            provider_used = "none"
        else:
            ctx.log.append(
                f"remote cue service failed ({type(exc).__name__}: {exc}), "
                "falling back to built-in silencedetect"
            )
            provider_used = "silence"
            fallback = SilenceDetectProvider(
                noise_db=float(ctx.settings.get("cue_silence_noise_db") or -35.0),
                min_silence_s=float(ctx.settings.get("cue_min_silence_s") or 1.0),
            )
            try:
                cues = await fallback.detect(ctx.original_path)
            except Exception as exc2:
                ctx.log.append(
                    f"silencedetect fallback failed ({type(exc2).__name__}: {exc2}), continuing without cues"
                )
                cues = []
                provider_used = "none"
    elapsed = time.monotonic() - started

    from_transcript = _transcript_music(ctx, cues)
    if from_transcript:
        cues = sorted([*cues, *from_transcript], key=lambda c: (c.start, c.end))
        ctx.log.append(f"cues: added {len(from_transcript)} music range(s) reported by the transcription provider")

    tmp = ctx.cues_path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(cues_to_json(cues)), encoding="utf-8")
    tmp.replace(ctx.cues_path)

    ctx.metrics["cue_count"] = len(cues)
    ctx.metrics["cue_seconds"] = round(sum(c.duration for c in cues), 1)
    ctx.metrics["cue_provider_used"] = provider_used
    ctx.log.append(
        f"cues ({provider_used}): {len(cues)} non-speech region(s), "
        f"{sum(c.duration for c in cues):.0f}s total, in {elapsed:.0f}s"
    )
