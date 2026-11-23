"""Rebuild detection inputs from stored detection prompts.

Retention deletes an expired episode's transcript and cue files, but every
detection request is kept in llm_calls, and its user message holds the
transcript exactly as the LLM saw it: one `[start-end] text` line per
transcript segment, and cue lines interleaved when cue annotations were on.
That is enough to re-run detection on the episode.

What can't be recovered: word timestamps (so boundary refinement is a no-op),
and cues shorter than cue_min_prompt_duration_s (never in the prompt).
Timestamps carry the prompt's 0.1 s precision. Re-chunking a rebuilt
transcript reproduces the original prompt text, except that a cue line's
printed duration can differ by 0.1 s (it is recomputed from rounded ends).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..cues.base import Cue
from ..transcription.base import Transcript, TranscriptSegment

_TRANSCRIPT_MARKER = "\n\nTranscript:\n"
_COVERS_RE = re.compile(r"This transcript covers (\d+)s to (\d+)s of the episode\.")
_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] ?(.*)$")
_CUE_RE = re.compile(r"^\[(SILENCE|MUSIC|NOISE)\] \(\d+(?:\.\d+)?s\)$")


@dataclass
class ChunkPrompt:
    start_s: int
    end_s: int
    user_content: str


@dataclass
class Rebuilt:
    transcript: Transcript
    cues: list[Cue]
    chunks: int


def is_detection_prompt(messages: list[dict]) -> bool:
    return (
        len(messages) >= 2
        and messages[0].get("role") == "system"
        and messages[1].get("role") == "user"
        and _TRANSCRIPT_MARKER in str(messages[1].get("content", ""))
    )


def latest_run(user_contents: list[str]) -> list[ChunkPrompt]:
    """The chunks of the most recent detection run, in order.

    `user_contents` is the detection user message of every stored call, oldest
    first. A retry repeats its chunk's message verbatim and is folded in. A
    chunk that doesn't start after the previous one begins a new run.
    """
    runs: list[list[ChunkPrompt]] = []
    for content in user_contents:
        m = _COVERS_RE.search(content)
        if not m:
            continue
        chunk = ChunkPrompt(int(m.group(1)), int(m.group(2)), content)
        current = runs[-1] if runs else None
        if current and current[-1].user_content == content:
            continue  # retry of the same chunk
        if current and chunk.start_s > current[-1].start_s and chunk.end_s > current[-1].end_s:
            current.append(chunk)
        else:
            runs.append([chunk])
    return runs[-1] if runs else []


def parse_lines(user_content: str) -> tuple[list[TranscriptSegment], list[Cue]]:
    body = user_content[user_content.rindex(_TRANSCRIPT_MARKER) + len(_TRANSCRIPT_MARKER):]
    segments: list[TranscriptSegment] = []
    cues: list[Cue] = []
    for raw in body.split("\n"):
        m = _LINE_RE.match(raw)
        if not m:
            continue
        start, end, rest = float(m.group(1)), float(m.group(2)), m.group(3)
        cue = _CUE_RE.match(rest)
        if cue:
            cues.append(Cue(start=start, end=end, kind=cue.group(1).lower()))
        else:
            # A speaker label renders as "SPEAKER_00: text". Keeping it inside
            # the text re-renders the identical prompt line, so don't split it.
            segments.append(TranscriptSegment(text=rest, start=start, end=end))
    return segments, cues


def rebuild(user_contents: list[str], duration_s: float | None) -> Rebuilt | None:
    """Detection inputs of the latest run, or None if there is no usable prompt."""
    chunks = latest_run(user_contents)
    if not chunks:
        return None
    seen_segments: dict[tuple[float, float, str], TranscriptSegment] = {}
    seen_cues: dict[tuple[float, float, str], Cue] = {}
    for chunk in chunks:
        segments, cues = parse_lines(chunk.user_content)
        # chunks overlap by ~60 s, the overlap repeats lines verbatim
        for s in segments:
            seen_segments.setdefault((s.start, s.end, s.text), s)
        for c in cues:
            seen_cues.setdefault((c.start, c.end, c.kind), c)
    segments = sorted(seen_segments.values(), key=lambda s: (s.start, s.end))
    if not segments:
        return None
    duration = duration_s or max(s.end for s in segments)
    return Rebuilt(
        transcript=Transcript(language=None, duration=duration, segments=segments),
        cues=sorted(seen_cues.values(), key=lambda c: (c.start, c.end)),
        chunks=len(chunks),
    )
