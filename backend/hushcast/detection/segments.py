"""Pure post-processing of LLM-detected ad segments.

Order: clamp -> snap to transcript boundaries -> threshold filters -> merge ->
cue bridging/edge extension -> word-boundary refinement -> circuit breaker.
All functions are side-effect free and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import refine as refine_mod


@dataclass(frozen=True)
class AdSegment:
    start: float
    end: float
    category: str
    confidence: float
    reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class DetectionRejected(Exception):
    """Detection result failed sanity checks, do not cut with it."""


# Circuit-breaker ceiling: refuse to cut when detection would remove more than
# this share of an episode.
MAX_REMOVED_PCT = 50.0


def clamp(segments: list[AdSegment], duration: float) -> list[AdSegment]:
    out = []
    for s in segments:
        start = max(0.0, min(s.start, duration))
        end = max(0.0, min(s.end, duration))
        if end > start:
            out.append(replace(s, start=start, end=end))
    return out


def snap_to_boundaries(
    segments: list[AdSegment], boundaries: list[float], tolerance_s: float
) -> list[AdSegment]:
    """Snap each start/end to the nearest transcript boundary within tolerance.

    A segment whose start or end has no boundary within tolerance is discarded
    (a strong sign the LLM hallucinated the timestamp).
    """
    if not boundaries:
        return segments
    sorted_bounds = sorted(boundaries)

    def nearest(t: float) -> float | None:
        best, best_dist = None, tolerance_s
        # binary-search-free linear scan is fine: boundary counts are small (~1k)
        for b in sorted_bounds:
            d = abs(b - t)
            if d <= best_dist:
                best, best_dist = b, d
            elif b > t + tolerance_s:
                break
        return best

    out = []
    for s in segments:
        start = nearest(s.start)
        end = nearest(s.end)
        if start is None or end is None or end <= start:
            continue
        out.append(replace(s, start=start, end=end))
    return out


def filter_thresholds(
    segments: list[AdSegment], min_confidence: float, min_duration_s: float
) -> list[AdSegment]:
    return [s for s in segments if s.confidence >= min_confidence and s.duration >= min_duration_s]


def merge(segments: list[AdSegment], gap_s: float) -> list[AdSegment]:
    """Merge overlapping segments and segments separated by less than gap_s.

    Handles duplicates coming from chunk overlaps. The merged segment keeps
    the max confidence and the first non-empty reason. Category prefers the
    longer contributor.
    """
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    merged = [ordered[0]]
    for s in ordered[1:]:
        last = merged[-1]
        if s.start <= last.end + gap_s:
            category = last.category if (last.end - last.start) >= s.duration else s.category
            merged[-1] = AdSegment(
                start=last.start,
                end=max(last.end, s.end),
                category=category,
                confidence=max(last.confidence, s.confidence),
                reason=last.reason or s.reason,
            )
        else:
            merged.append(s)
    return merged


def _uncovered(start: float, end: float, intervals: list[tuple[float, float]]) -> float:
    """Length of [start, end] not covered by the union of the given intervals."""
    if end <= start:
        return 0.0
    uncovered = end - start
    cursor = start
    for a, b in sorted(intervals):
        if b <= cursor:
            continue
        if a >= end:
            break
        lo = max(a, cursor)
        hi = min(b, end)
        if hi > lo:
            uncovered -= hi - lo
            cursor = hi
    return uncovered


def bridge_over_cues(
    segments: list[AdSegment],
    cue_intervals: list[tuple[float, float]],
    *,
    max_bridge_gap_s: float,
    coverage_slack_s: float = 1.0,
) -> list[AdSegment]:
    """Merge consecutive ad segments whose gap is non-speech (music/silence).

    Ad breaks are often several spots separated by music stings. The LLM sees
    no transcript there and returns them as separate segments. When the gap is
    short enough and covered by non-speech cues (up to `coverage_slack_s` of
    uncovered time, absorbing boundary-snap rounding), treat it as one break.
    """
    if not segments or not cue_intervals or max_bridge_gap_s <= 0:
        return segments
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    out = [ordered[0]]
    for s in ordered[1:]:
        last = out[-1]
        gap = s.start - last.end
        if 0 < gap <= max_bridge_gap_s and _uncovered(last.end, s.start, cue_intervals) <= coverage_slack_s:
            category = last.category if (last.end - last.start) >= s.duration else s.category
            out[-1] = AdSegment(
                start=last.start,
                end=max(last.end, s.end),
                category=category,
                confidence=max(last.confidence, s.confidence),
                reason=last.reason or s.reason,
            )
        else:
            out.append(s)
    return out


def extend_to_edges(
    segments: list[AdSegment],
    cue_intervals: list[tuple[float, float]],
    *,
    duration: float,
    max_edge_extension_s: float,
    coverage_slack_s: float = 1.0,
) -> list[AdSegment]:
    """Extend an ad at the episode edge across a leading/trailing music sting.

    A pre-roll ad often sits behind an intro sting the transcript can't see,
    same for outros. If the first segment starts within `max_edge_extension_s`
    of 0 and everything before it is non-speech, extend it to 0 (symmetric for
    the last segment vs `duration`).
    """
    if not segments or not cue_intervals or max_edge_extension_s <= 0:
        return segments
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    first = ordered[0]
    if 0 < first.start <= max_edge_extension_s and _uncovered(0.0, first.start, cue_intervals) <= coverage_slack_s:
        ordered[0] = replace(first, start=0.0)
    last = ordered[-1]
    if duration > 0 and 0 < duration - last.end <= max_edge_extension_s:
        if _uncovered(last.end, duration, cue_intervals) <= coverage_slack_s:
            ordered[-1] = replace(last, end=duration)
    return ordered


def check_removed_fraction(segments: list[AdSegment], duration: float, max_removed_pct: float) -> None:
    if duration <= 0:
        return
    removed = sum(s.duration for s in segments)
    pct = 100.0 * removed / duration
    if pct > max_removed_pct:
        raise DetectionRejected(
            f"detected segments would remove {pct:.1f}% of the episode "
            f"(limit {max_removed_pct:.0f}%), refusing: the LLM likely misfired"
        )


def postprocess(
    raw: list[AdSegment],
    *,
    duration: float,
    boundaries: list[float],
    snap_tolerance_s: float,
    min_confidence: float,
    min_duration_s: float,
    merge_gap_s: float,
    max_removed_pct: float = MAX_REMOVED_PCT,
    cue_intervals: list[tuple[float, float]] | None = None,
    bridge_max_gap_s: float = 0.0,
    edge_max_extension_s: float = 0.0,
    refine_gaps: list[refine_mod.WordGap] | None = None,
    refine_window_s: float = 2.0,
    refine_min_gap_s: float = 0.15,
) -> list[AdSegment]:
    segments = clamp(raw, duration)
    segments = snap_to_boundaries(segments, boundaries, snap_tolerance_s)
    segments = filter_thresholds(segments, min_confidence, min_duration_s)
    segments = merge(segments, merge_gap_s)
    if cue_intervals:
        segments = bridge_over_cues(segments, cue_intervals, max_bridge_gap_s=bridge_max_gap_s)
        segments = extend_to_edges(
            segments, cue_intervals, duration=duration, max_edge_extension_s=edge_max_extension_s
        )
    if refine_gaps:
        segments = refine_mod.refine_boundaries(
            segments, refine_gaps, window_s=refine_window_s, min_gap_s=refine_min_gap_s
        )
    check_removed_fraction(segments, duration, max_removed_pct)
    return segments


def keep_ranges(segments: list[AdSegment], duration: float) -> list[tuple[float, float]]:
    """Complement of the ad segments over [0, duration]."""
    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for s in sorted(segments, key=lambda s: s.start):
        if s.start > cursor:
            ranges.append((cursor, s.start))
        cursor = max(cursor, s.end)
    if cursor < duration:
        ranges.append((cursor, duration))
    return ranges
