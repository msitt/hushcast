"""Word-level boundary refinement for detected ad segments.

After snapping to coarse transcript-segment boundaries, a cut can still clip
the first word of content or leave a breath of ad audio behind. When word
timestamps are available, each boundary is moved into a nearby inter-word
silence (the wider and closer the better) so the cut lands in a production
gap instead of mid-word.

Pure functions, no I/O. Mirrors segments.py and is unit-tested the same way.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from ..transcription.base import Transcript, Word


@dataclass(frozen=True)
class WordGap:
    """Silence between two consecutive words: [start, end] = [prev.end, next.start]."""

    start: float
    end: float

    @property
    def width(self) -> float:
        return self.end - self.start

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2


def collect_words(transcript: Transcript) -> list[Word]:
    """All words across segments, sorted by start. Empty when word timestamps are off."""
    words = [w for s in transcript.segments for w in s.words]
    words.sort(key=lambda w: (w.start, w.end))
    return words


def word_gaps(words: list[Word], duration: float | None = None) -> list[WordGap]:
    """Silences between consecutive words, plus the episode edges.

    With `duration`, the pre-roll before the first word and the tail after the
    last one count as gaps too: a boundary at or near the episode edge is
    already a perfect cut point, and without these virtual gaps the only
    candidates near it are pauses inside the ad, so refinement would drag the
    cut inward past the ad's opening words.
    """
    gaps: list[WordGap] = []
    if words and words[0].start > 0:
        gaps.append(WordGap(start=0.0, end=words[0].start))
    for prev, nxt in zip(words, words[1:], strict=False):
        if nxt.start > prev.end:
            gaps.append(WordGap(start=prev.end, end=nxt.start))
    if words and duration is not None and duration > words[-1].end:
        gaps.append(WordGap(start=words[-1].end, end=duration))
    return gaps


def _best_gap(
    gaps: list[WordGap], boundary: float, window_s: float, min_gap_s: float, *, outward: float
) -> float | None:
    """Best qualifying cut point near `boundary`.

    Gaps are scored `width * exp(-distance / tau)` so a wider gap farther out
    must be proportionally wider to beat a decent gap right at the boundary,
    plain widest-in-window used to wander seconds away into a mid-sentence
    breath pause. Distance is measured to the gap's nearest edge (zero when
    the boundary already lies inside the gap): a boundary sitting at the edge
    of a long production silence must not disqualify that silence just
    because its midpoint is far away. tau is half the window, halved again
    for moves in the `outward` direction (sign of target - boundary that
    would expand the cut): clipping surrounding content is worse than leaving
    a sliver of ad. The returned point is the gap's midpoint, clamped to
    within `window_s` of the boundary so a very wide gap never drags the cut
    far from where detection put it (the clamp always lands inside the gap).
    """
    if window_s <= 0:
        return None
    tau = window_s / 2
    best: float | None = None
    best_score = 0.0
    best_dist = 0.0
    for g in gaps:
        if g.width < min_gap_s:
            continue
        if boundary < g.start:
            d = g.start - boundary
        elif boundary > g.end:
            d = boundary - g.end
        else:
            d = 0.0
        if d > window_s:
            continue
        target = min(max(g.midpoint, boundary - window_s), boundary + window_s)
        t = tau / 2 if (target - boundary) * outward > 0 else tau
        score = g.width * math.exp(-d / t)
        if best is None or score > best_score or (score == best_score and d < best_dist):
            best, best_score, best_dist = target, score, d
    return best


def refine_boundaries(
    segments: list,
    gaps: list[WordGap],
    *,
    window_s: float,
    min_gap_s: float,
) -> list:
    """Move each segment's start/end to a nearby inter-word silence.

    Works on any dataclass with float `start`/`end` fields. Identity when
    `gaps` is empty (word timestamps absent). Never drops a segment: a
    refinement that would invert or overlap a neighbor is discarded and the
    original boundary kept, so input and output lists always align 1:1.
    """
    if not gaps or not segments:
        return segments
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    out: list = []
    for i, s in enumerate(ordered):
        start = _best_gap(gaps, s.start, window_s, min_gap_s, outward=-1.0)
        end = _best_gap(gaps, s.end, window_s, min_gap_s, outward=1.0)
        if start is None:
            start = s.start
        if end is None:
            end = s.end
        if end <= start:
            start, end = s.start, s.end
        # never cross into the previous refined segment or the next original one
        if out and start < out[-1].end:
            start = s.start
        if i + 1 < len(ordered) and end > ordered[i + 1].start:
            end = s.end
        if end <= start:
            start, end = s.start, s.end
        out.append(replace(s, start=start, end=end))
    return out


def count_moved(before: list, after: list) -> int:
    """Number of boundaries that refinement actually moved."""
    moved = 0
    for b, a in zip(sorted(before, key=lambda s: s.start), sorted(after, key=lambda s: s.start), strict=True):
        moved += (b.start != a.start) + (b.end != a.end)
    return moved
