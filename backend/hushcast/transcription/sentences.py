"""Split transcript segments at sentence boundaries.

Whisper-style ASR may pack an ad's last sentence and the content's first
sentence into one segment. Detection can only cut at segment boundaries, so a
straddling segment forces a wrong cut on one side. Splitting at sentence ends
makes the seam a real boundary the LLM can cite and snapping can accept.

Split timing uses word timestamps when present (exact) and falls back to
prorating the segment's duration by character position otherwise. Pure
functions, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .base import Transcript, TranscriptSegment

# Tokens that end with a period but don't end a sentence: initialisms with
# internal periods (U.S., e.g., i.e.) and common abbreviated titles/suffixes.
_ABBREVIATION_RE = re.compile(r"^(?:[A-Za-z]\.){2,}$")
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "etc.",
    "inc.", "ltd.", "co.", "corp.", "no.", "vol.", "gen.", "sen.", "rep.", "gov.",
}

_SENTENCE_END_RE = re.compile(r"[.!?][\"'”’)]*$")
_NEXT_START_RE = re.compile(r"^[\"'“‘(]*[A-Z0-9]")


def _ends_sentence(token: str, next_token: str) -> bool:
    if not _SENTENCE_END_RE.search(token):
        return False
    bare = token.strip("\"'“”‘’()")
    if _ABBREVIATION_RE.match(bare) or bare.lower() in _ABBREVIATIONS:
        return False
    return bool(_NEXT_START_RE.match(next_token))


def _split_indices(tokens: list[str]) -> list[int]:
    """Indices i such that tokens[i] ends a sentence and tokens[i+1] starts one."""
    return [i for i in range(len(tokens) - 1) if _ends_sentence(tokens[i], tokens[i + 1])]


def _split_segment(seg: TranscriptSegment) -> list[TranscriptSegment]:
    tokens = seg.text.split()
    cuts = _split_indices(tokens)
    if not cuts:
        return [seg]

    # words align 1:1 with whitespace tokens for typical ASR output, on any
    # mismatch fall back to prorating by character position
    aligned = len(seg.words) == len(tokens)
    total_chars = len(seg.text)
    duration = max(0.0, seg.end - seg.start)

    pieces: list[TranscriptSegment] = []
    piece_start_tok = 0
    piece_start_time = seg.start
    for cut in [*cuts, len(tokens) - 1]:
        piece_tokens = tokens[piece_start_tok : cut + 1]
        if cut == len(tokens) - 1:
            piece_end_time = seg.end
        elif aligned:
            piece_end_time = seg.words[cut].end
        else:
            chars_before = len(" ".join(tokens[: cut + 1]))
            piece_end_time = seg.start + duration * (chars_before / total_chars) if total_chars else seg.start
        piece_end_time = min(max(piece_end_time, piece_start_time), seg.end)

        next_start_time = piece_end_time
        if cut != len(tokens) - 1 and aligned:
            next_start_time = max(piece_end_time, seg.words[cut + 1].start)

        pieces.append(
            TranscriptSegment(
                text=" ".join(piece_tokens),
                start=piece_start_time,
                end=piece_end_time,
                speaker=seg.speaker,
                words=list(seg.words[piece_start_tok : cut + 1]) if aligned else [],
            )
        )
        piece_start_tok = cut + 1
        piece_start_time = next_start_time

    # an alignment fallback dropped per-word data, keep the original words on
    # the piece whose time range contains each word so nothing is lost
    if not aligned and seg.words:
        for w in seg.words:
            mid = (w.start + w.end) / 2
            target = min(pieces, key=lambda p: 0.0 if p.start <= mid <= p.end else min(abs(mid - p.start), abs(mid - p.end)))
            target.words.append(w)
    return pieces


def split_at_sentences(transcript: Transcript) -> Transcript:
    """New Transcript with every multi-sentence segment split at sentence ends."""
    out: list[TranscriptSegment] = []
    for seg in transcript.segments:
        out.extend(_split_segment(seg))
    if len(out) == len(transcript.segments):
        return transcript
    return replace(transcript, segments=out)


def count_splits(before: Transcript, after: Transcript) -> int:
    return len(after.segments) - len(before.segments)
