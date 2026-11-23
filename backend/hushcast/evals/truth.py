"""Ground-truth ad spans from an episode's segments and corrections."""
from __future__ import annotations

from dataclasses import dataclass

from .intervals import subtract, union


@dataclass(frozen=True)
class SegmentRow:
    """The parts of a Segment row the truth depends on."""

    start_s: float
    end_s: float
    category: str
    kept: bool
    source: str  # llm | manual | promo


@dataclass(frozen=True)
class TruthSpan:
    start: float
    end: float
    category: str


@dataclass(frozen=True)
class Truth:
    spans: list[TruthSpan]
    promo: bool  # the episode is promo-only: nothing should be cut


def effective_truth(rows: list[SegmentRow]) -> Truth:
    """What the episode should have had cut, per the segments as they stand.

    Detected segments not marked "not an ad", plus manually added missed ads,
    minus manual "not an ad" trims inside detected blocks. Overlaps are merged
    and the earliest-starting contributor's category wins.

    Promo-only episodes (source=promo rows) are their own outcome: the truth is
    "promo, cut nothing" and the spans are kept for reference only.
    """
    if any(r.source == "promo" for r in rows):
        promo_rows = sorted((r for r in rows if r.source == "promo"), key=lambda r: r.start_s)
        return Truth(
            spans=[TruthSpan(r.start_s, r.end_s, r.category) for r in promo_rows],
            promo=True,
        )
    positive = [r for r in rows if not r.kept]
    trims = [(r.start_s, r.end_s) for r in rows if r.kept and r.source == "manual"]
    covered = subtract(union([(r.start_s, r.end_s) for r in positive]), trims)
    spans = []
    for start, end in covered:
        overlapping = sorted(
            (r for r in positive if r.start_s < end and r.end_s > start), key=lambda r: r.start_s
        )
        category = overlapping[0].category if overlapping else "ad"
        spans.append(TruthSpan(round(start, 3), round(end, 3), category))
    return Truth(spans=spans, promo=False)
