"""Turn a raw LLM detection result into the final segments and outcome.

Pure (no DB, no I/O), shared by the detect pipeline step and the eval runner
so an eval scores exactly what the pipeline would have served.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..cues.base import Cue
from ..transcription.base import Transcript
from . import prompts, refine
from . import segments as seg
from .llm import DetectionResult

# cut: segments are cut as usual. promo: promo-only episode, nothing is cut.
# review: the circuit breaker tripped, a human decides.
Outcome = Literal["cut", "promo", "review"]


@dataclass
class Finalized:
    segments: list[seg.AdSegment]
    outcome: Outcome
    removed_pct: float
    rejection: str | None = None  # circuit breaker message when outcome is review


def finalize(
    result: DetectionResult,
    *,
    transcript: Transcript,
    cues: list[Cue] | None,
    duration: float,
    settings: dict[str, Any],
) -> Finalized:
    # boundary refinement is a no-op when the transcript has no word timestamps
    refine_gaps = refine.word_gaps(refine.collect_words(transcript), duration) or None

    final = seg.postprocess(
        result.segments,
        duration=duration,
        boundaries=prompts.boundaries(transcript),
        snap_tolerance_s=float(settings["snap_tolerance_s"]),
        min_duration_s=float(settings["min_duration_s"]),
        merge_gap_s=float(settings["merge_gap_s"]),
        cue_intervals=[(c.start, c.end) for c in cues] if cues else None,
        bridge_max_gap_s=float(settings["cue_bridge_max_gap_s"]),
        edge_max_extension_s=float(settings["cue_edge_max_extension_s"]),
        refine_gaps=refine_gaps,
        refine_window_s=float(settings["refine_window_s"]),
        refine_min_gap_s=float(settings["refine_min_gap_s"]),
        max_removed_pct=None,  # the circuit breaker runs below, after the promo check
    )
    pct = seg.removed_pct(final, duration)

    # A promo-only episode (trailer, cross-promo) is a distinct outcome, not a
    # misfire: the LLM says so and the segments back it up.
    if result.whole_episode_promo and pct >= seg.PROMO_MIN_COVERAGE_PCT:
        return Finalized(segments=final, outcome="promo", removed_pct=pct)

    try:
        seg.check_removed_fraction(final, duration, seg.MAX_REMOVED_PCT)
    except seg.DetectionRejected as exc:
        return Finalized(segments=final, outcome="review", removed_pct=pct, rejection=str(exc))
    return Finalized(segments=final, outcome="cut", removed_pct=pct)
