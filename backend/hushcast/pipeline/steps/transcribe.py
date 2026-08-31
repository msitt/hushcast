"""Transcribe the original audio (idempotent: skips when transcript exists)."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from ...audio import ffmpeg
from ...db import session_factory
from ...models import TranscriptionCall, utcnow
from ...transcription import sentences
from ...transcription.base import Transcript, TranscriptionProvider
from ...transcription.openai_compat import build_transcriber
from ..context import EpisodeContext

# Split ratio kept below 1.0 to give slack for opus/ogg container overhead so
# each part still lands under the cap even though the source bitrate is only
# an average, not a hard per-second guarantee.
SPLIT_SAFETY_MARGIN = 0.95

# Extra audio each part carries into its neighbors so a word straddling a cut
# point isn't truncated at the very edge of every file it appears in.
SPLIT_OVERLAP_S = 8.0


def _record_raw_call(
    raw_calls: list[dict],
    transcriber: TranscriptionProvider,
    ctx: EpisodeContext,
    *,
    part: int,
    parts: int,
    offset_s: float,
    owned_start_s: float | None,
    owned_end_s: float | None,
    elapsed_s: float,
    upload_bytes: int | None = None,
) -> None:
    """Append one provider call's raw response to the debug log (best effort)."""
    payload = getattr(transcriber, "last_raw_payload", None)
    # Audio seconds this call covered: what the provider says it processed,
    # falling back to the part's owned window (excludes overlap slack).
    audio_s: float | None = None
    if isinstance(payload, dict) and isinstance(payload.get("duration"), (int, float)):
        audio_s = float(payload["duration"])
    if audio_s is None and owned_start_s is not None and owned_end_s is not None:
        audio_s = owned_end_s - owned_start_s
    raw_calls.append(
        {
            "recorded_at": utcnow().isoformat(),
            "part": part,
            "parts": parts,
            "offset_s": offset_s,
            "owned_start_s": owned_start_s,
            "owned_end_s": owned_end_s,
            "elapsed_s": round(elapsed_s, 1),
            "audio_s": audio_s,
            "upload_bytes": upload_bytes,
            "url": str(ctx.settings.get("transcription_base_url") or ""),
            "model": str(ctx.settings.get("transcription_model") or ""),
            "payload": payload,
        }
    )


async def _transcribe_in_parts(
    ctx: EpisodeContext,
    transcriber: TranscriptionProvider,
    upload_path: Path,
    cap_bytes: float,
    raw_calls: list[dict],
) -> Transcript:
    """The transcoded upload is still over the provider's size cap (typically a
    very long episode). Slice it into consecutive parts that each land under
    the cap, transcribe them individually, and stitch the results back into
    one transcript with timestamps shifted to the original timeline.
    """
    total_duration = await ffmpeg.probe_duration(upload_path)
    size = upload_path.stat().st_size
    num_parts = max(2, math.ceil(size / (cap_bytes * SPLIT_SAFETY_MARGIN)))
    part_seconds = total_duration / num_parts
    # Keep the overlap sane if num_parts made for very short owned windows.
    overlap = min(SPLIT_OVERLAP_S, part_seconds / 4)
    parts = await ffmpeg.split_by_duration(
        upload_path, ctx.config.original_audio_dir, f"{ctx.episode_id}.upload", ".ogg",
        part_seconds, total_duration, overlap_seconds=overlap,
    )
    ctx.log.append(
        f"transcoded upload still exceeds cap ({size / 1_000_000:.1f} MB), "
        f"split into {len(parts)} parts of ~{part_seconds / 60:.1f} min each ({overlap:.0f}s overlap)"
    )
    try:
        transcripts = []
        for i, part in enumerate(parts):
            part_started = time.monotonic()
            transcripts.append(await transcriber.transcribe(part.path))
            _record_raw_call(
                raw_calls, transcriber, ctx,
                part=i + 1, parts=len(parts),
                offset_s=part.content_start,
                owned_start_s=part.owned_start, owned_end_s=part.owned_end,
                elapsed_s=time.monotonic() - part_started,
                upload_bytes=part.path.stat().st_size,
            )
    finally:
        for part in parts:
            part.path.unlink(missing_ok=True)
    return Transcript.concat(
        transcripts,
        [part.content_start for part in parts],
        owned_ranges=[(part.owned_start, part.owned_end) for part in parts],
    )


def load_transcript(ctx: EpisodeContext) -> Transcript | None:
    if not ctx.transcript_path.exists():
        return None
    try:
        return Transcript.from_json_dict(json.loads(ctx.transcript_path.read_text(encoding="utf-8")))
    except (ValueError, KeyError):
        return None


async def run(ctx: EpisodeContext) -> None:
    if load_transcript(ctx) is not None:
        ctx.log.append("transcribe skipped: transcript already on disk")
        return
    assert ctx.original_path is not None

    upload_path = ctx.original_path
    max_mb = float(ctx.settings["transcription_max_upload_mb"] or 0)
    cap_bytes = max_mb * 1_000_000
    if max_mb > 0 and upload_path.stat().st_size > cap_bytes:
        small = ctx.config.original_audio_dir / f"{ctx.episode_id}.upload.ogg"
        if not small.exists():
            ctx.log.append(
                f"source exceeds provider upload cap ({max_mb} MB), transcoding to 16kHz mono opus"
            )
            await ffmpeg.transcode_for_upload(upload_path, small)
        upload_path = small

    transcriber = build_transcriber(ctx.settings)
    started = time.monotonic()
    upload_size = upload_path.stat().st_size
    raw_calls: list[dict] = []
    if max_mb > 0 and upload_size > cap_bytes:
        transcript = await _transcribe_in_parts(ctx, transcriber, upload_path, cap_bytes, raw_calls)
    else:
        transcript = await transcriber.transcribe(upload_path)
        _record_raw_call(
            raw_calls, transcriber, ctx,
            part=1, parts=1, offset_s=0.0, owned_start_s=None, owned_end_s=None,
            elapsed_s=time.monotonic() - started,
            upload_bytes=upload_size,
        )
        if raw_calls[-1]["audio_s"] is None:
            raw_calls[-1]["audio_s"] = transcript.duration
    elapsed = time.monotonic() - started
    if not transcript.segments:
        raise RuntimeError("transcription returned no segments")

    # Split multi-sentence segments so ad/content seams inside a segment become
    # boundaries detection can actually cut at, persisted so the UI, detection,
    # and corrections all share the same segmentation.
    split = sentences.split_at_sentences(transcript)
    if len(split.segments) != len(transcript.segments):
        ctx.log.append(
            f"split {len(transcript.segments)} segments into {len(split.segments)} at sentence boundaries"
        )
    transcript = split

    tmp = ctx.transcript_path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(transcript.to_json_dict()), encoding="utf-8")
    tmp.replace(ctx.transcript_path)

    # raw per-call provider responses, for the debug view, losing them is fine.
    # Calls from earlier runs are kept so a reprocess appends rather than replaces.
    try:
        try:
            previous = json.loads(ctx.raw_transcript_path.read_text(encoding="utf-8")).get("calls", [])
        except (OSError, ValueError):
            previous = []
        raw_tmp = ctx.raw_transcript_path.with_suffix(".tmp.json")
        raw_tmp.write_text(json.dumps({"calls": previous + raw_calls}), encoding="utf-8")
        raw_tmp.replace(ctx.raw_transcript_path)
    except OSError as exc:
        ctx.log.append(f"could not write raw transcription log: {exc}")

    # usage accounting rows (System page), failing to record never fails the step
    try:
        factory = session_factory()
        async with factory() as session:
            for call in raw_calls:
                session.add(
                    TranscriptionCall(
                        episode_id=ctx.episode_id,
                        url=call["url"],
                        model=call["model"],
                        part=call["part"],
                        parts=call["parts"],
                        audio_s=call["audio_s"],
                        upload_bytes=call["upload_bytes"],
                        elapsed_s=call["elapsed_s"],
                    )
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        ctx.log.append(f"could not record transcription usage: {exc}")

    ctx.metrics["transcribe_seconds"] = round(elapsed, 1)
    ctx.metrics["transcript_segments"] = len(transcript.segments)
    ctx.log.append(
        f"transcribed {len(transcript.segments)} segments "
        f"({transcript.duration:.0f}s audio) in {elapsed:.0f}s"
    )
