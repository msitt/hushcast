"""Transcript rendering and prompt assembly for ad detection."""
from __future__ import annotations

from dataclasses import dataclass

from ..cues.base import Cue
from ..transcription.base import Transcript

# silence and noenergy are the same thing to the LLM, keep the vocabulary small
_CUE_LABELS = {"silence": "SILENCE", "noenergy": "SILENCE", "music": "MUSIC", "noise": "NOISE"}

CUE_EXPLANATION = (
    "Some lines are audio cues, like `[122.4-127.6] [MUSIC] (5.2s)`: they mark "
    "non-speech regions (MUSIC, SILENCE, NOISE) detected in the audio. Ads are "
    "often preceded or followed by music stings or silence. Use cues to judge "
    "where an ad break starts and ends. Cue timestamps are context only: a "
    "segment's start/end must still come from transcript lines, never from cue lines."
)


@dataclass(frozen=True)
class Line:
    text: str
    start: float
    end: float


def render_lines(transcript: Transcript) -> list[str]:
    return [line.text for line in build_lines(transcript)]


def build_lines(
    transcript: Transcript,
    cues: list[Cue] | None = None,
    *,
    min_cue_duration_s: float = 0.0,
) -> list[Line]:
    """Transcript lines, optionally merge-sorted with cue annotation lines."""
    lines = []
    for s in transcript.segments:
        speaker = f" {s.speaker}:" if s.speaker else ""
        lines.append(Line(text=f"[{s.start:.1f}-{s.end:.1f}]{speaker} {s.text}", start=s.start, end=s.end))
    if cues:
        for c in cues:
            if c.duration < min_cue_duration_s:
                continue
            label = _CUE_LABELS.get(c.kind, c.kind.upper())
            lines.append(
                Line(text=f"[{c.start:.1f}-{c.end:.1f}] [{label}] ({c.duration:.1f}s)", start=c.start, end=c.end)
            )
        # only sort when interleaving: callers like corrections.py rely on
        # cue-less lines pairing 1:1, in order, with transcript.segments
        lines.sort(key=lambda line: (line.start, line.end))
    return lines


def boundaries(transcript: Transcript) -> list[float]:
    """All segment starts/ends: the only timestamps the LLM may legally use.

    Deliberately transcript-only: cue timestamps are prompt context, not legal
    cut points.
    """
    out: set[float] = set()
    for s in transcript.segments:
        out.add(round(s.start, 1))
        out.add(round(s.end, 1))
    return sorted(out)


def estimate_tokens(text: str) -> int:
    return len(text) // 4  # chars/4 is a safe, provider-agnostic approximation


@dataclass
class Chunk:
    text: str
    start_s: float
    end_s: float


def chunk_transcript(
    transcript: Transcript,
    budget_tokens: int,
    overlap_s: float = 60.0,
    cues: list[Cue] | None = None,
    min_cue_duration_s: float = 0.0,
) -> list[Chunk]:
    """Single chunk when it fits the budget, otherwise split with time overlap."""
    lines = build_lines(transcript, cues, min_cue_duration_s=min_cue_duration_s)
    full = "\n".join(line.text for line in lines)
    if estimate_tokens(full) <= budget_tokens:
        return [Chunk(text=full, start_s=0.0, end_s=transcript.duration)]

    budget_chars = budget_tokens * 4
    chunks: list[Chunk] = []
    i = 0
    n = len(lines)
    while i < n:
        size = 0
        j = i
        while j < n and size + len(lines[j].text) + 1 <= budget_chars:
            size += len(lines[j].text) + 1
            j += 1
        j = max(j, i + 1)
        line_slice = lines[i:j]
        chunks.append(
            Chunk(
                text="\n".join(line.text for line in line_slice),
                start_s=line_slice[0].start,
                end_s=max(line.end for line in line_slice),
            )
        )
        if j >= n:
            break
        # step back so the next chunk overlaps the previous by ~overlap_s
        overlap_start = lines[j - 1].end - overlap_s
        k = j
        while k > i + 1 and lines[k - 1].start > overlap_start:
            k -= 1
        i = k
    return chunks


def build_messages(
    *,
    system_prompt: str,
    chunk: Chunk,
    podcast_title: str,
    episode_title: str,
    detection_hints: str | None,
    learned_hints: str | None = None,
    global_learned_hints: str | None = None,
    has_cues: bool = False,
) -> list[dict[str, str]]:
    user_parts = [
        f'Podcast: "{podcast_title}"',
        f'Episode: "{episode_title}"',
        f"This transcript covers {chunk.start_s:.0f}s to {chunk.end_s:.0f}s of the episode.",
    ]
    # Order matters: global guidance first, podcast-specific after, and the
    # instruction that podcast-specific wins on conflict.
    if global_learned_hints:
        user_parts.append(
            "Guidance learned from past corrections across all podcasts:\n" + global_learned_hints
        )
    if detection_hints:
        user_parts.append(f"Podcast-specific guidance from the user:\n{detection_hints}")
    if learned_hints:
        user_parts.append(
            "Guidance learned from past corrections of this podcast:\n" + learned_hints
        )
    if global_learned_hints and (detection_hints or learned_hints):
        user_parts.append("If podcast-specific guidance conflicts with global guidance, the podcast-specific guidance wins.")
    if has_cues:
        # in the user message (not the system prompt) so users who customized
        # the stored detection_prompt still get the cue explanation
        user_parts.append(CUE_EXPLANATION)
    user_parts.append("Transcript:\n" + chunk.text)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
