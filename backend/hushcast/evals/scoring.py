"""Score predicted ad cuts against verified truth.

The two raw error measures, in seconds of audio:
- content cut: non-ad audio the prediction removes (the worse error)
- ad missed: ad audio the prediction leaves in

weighted error = ad missed + content_weight * content cut, so a single number
ranks models while the raw pair stays visible. A collar around every truth
boundary is ignored for both: labels and transcript line edges are only
accurate to a second or two, and scoring that slack rewards luck.

Audio with no speech in the transcript at a truth boundary is ignored too, up
to ScoreConfig.edge_gap_s of it (EDGE_GAP_MAX_S by default): silence, and stretches the transcriber skipped
(Whisper can leave up to about 30 s blank around a music sting or an ad's
first line). The model sees only transcript lines and the label's edge inside
such a gap is a guess, so where a cut lands in it says nothing about the model.

Optional ranges are ignored the same way. They mark audio where cutting and
keeping are both acceptable (a network ident, a "coming up" teaser), so a
prediction is neither rewarded nor punished there. How much of them a model
cuts is reported on its own and stays out of every other number.

Promo-only episodes (truth or prediction) are scored on the outcome only.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .intervals import Interval, intersect, subtract, total, union

# Default for ScoreConfig.edge_gap_s: a speech-free gap at a truth boundary is
# not scored when at most this long. Longer ones are scored, since they likely
# hold real audio worth judging.
EDGE_GAP_MAX_S = 30.0
# how close a gap edge must be to a truth boundary to count as touching it
_TOUCH_S = 0.25

# A truth block counts as found when the prediction covers this share of it,
# and a predicted block as spurious when truth covers less than this share.
BLOCK_COVER = 0.5


@dataclass(frozen=True)
class ScoreConfig:
    collar_s: float = 1.0
    content_weight: float = 3.0
    blunder_s: float = 5.0  # an episode with more content cut than this is a blunder
    edge_gap_s: float = EDGE_GAP_MAX_S  # 0 scores every gap


@dataclass
class EpisodeScore:
    episode_id: int
    duration_s: float
    expected_outcome: str  # cut | promo
    outcome: str  # cut | promo | review | error
    seconds_scored: bool
    truth_ad_s: float = 0.0
    pred_ad_s: float = 0.0
    content_cut_s: float = 0.0
    missed_ad_s: float = 0.0
    optional_s: float = 0.0
    optional_cut_s: float = 0.0  # informational, not part of any error
    truth_blocks: int = 0
    found_blocks: int = 0
    spurious_blocks: int = 0
    # per truth block found: (pred start - truth start, pred end - truth end).
    # A negative start or positive end error cuts content. Optional audio
    # between the two edges does not count toward the distance.
    boundary_errors: list[tuple[float, float]] = field(default_factory=list)

    def weighted_error_s(self, cfg: ScoreConfig) -> float:
        return self.missed_ad_s + cfg.content_weight * self.content_cut_s

    def blunder(self, cfg: ScoreConfig) -> bool:
        return self.content_cut_s > cfg.blunder_s

    @property
    def outcome_ok(self) -> bool:
        # review is not ok: the episode waits for a human instead of being served
        return self.outcome == self.expected_outcome


def _collar(truth: list[Interval], collar_s: float, duration: float) -> list[Interval]:
    if collar_s <= 0:
        return []
    zones = []
    for start, end in truth:
        for b in (start, end):
            zones.append((max(0.0, b - collar_s), min(duration, b + collar_s)))
    return union(zones)


def silence_intervals(transcript: dict[str, Any] | None, cues: list[dict[str, Any]] | None) -> list[Interval]:
    """Audio with no transcribed speech: gaps between transcript lines, gaps
    between words when the transcript has word timestamps, and silence cues."""
    out: list[Interval] = []
    segments = sorted((transcript or {}).get("segments") or [], key=lambda s: s["start"])
    for prev, nxt in zip(segments, segments[1:], strict=False):
        out.append((float(prev["end"]), float(nxt["start"])))
    words = sorted((w for s in segments for w in s.get("words") or []), key=lambda w: w["start"])
    for prev, nxt in zip(words, words[1:], strict=False):
        out.append((float(prev["end"]), float(nxt["start"])))
    out += [(float(c["start"]), float(c["end"])) for c in cues or [] if c.get("kind") == "silence"]
    return [(round(a, 2), round(b, 2)) for a, b in union(out)]


def edge_gaps(
    truth: list[Interval], optional: list[Interval], silence: list[Interval], max_s: float
) -> list[Interval]:
    """The speech-free gaps, up to max_s long, that touch a truth or optional
    boundary: the audio scoring ignores beyond the collar."""
    if max_s <= 0:
        return []
    opt_u = union(optional)
    edges = [b for start, end in subtract(truth, opt_u) + opt_u for b in (start, end)]
    return [
        (s, e)
        for s, e in silence
        if e - s <= max_s and any(s - _TOUCH_S <= b <= e + _TOUCH_S for b in edges)
    ]


def _blocks(truth_u: list[Interval], opt_u: list[Interval]) -> list[Interval]:
    """Truth blocks for the block metrics. Truth ranges separated only by
    optional audio are one block, since a single cut across them is correct."""
    out = []
    for rs, re_ in union(truth_u + opt_u):
        inside = [(s, e) for s, e in truth_u if s >= rs and e <= re_]
        if inside:
            out.append((inside[0][0], inside[-1][1]))
    return out


def score_episode(
    *,
    episode_id: int,
    duration_s: float,
    truth: list[Interval],
    truth_promo: bool,
    predicted: list[Interval],
    outcome: str,
    cfg: ScoreConfig,
    optional: list[Interval] | None = None,
    silence: list[Interval] | None = None,
) -> EpisodeScore:
    expected = "promo" if truth_promo else "cut"
    if truth_promo:
        return EpisodeScore(episode_id, duration_s, expected, outcome, seconds_scored=False)

    opt_u = union(optional or [])
    truth_u = subtract(truth, opt_u)
    # promo and error outcomes cut nothing
    pred_u = union(predicted) if outcome in ("cut", "review") else []
    zones = union(
        _collar(truth_u + opt_u, cfg.collar_s, duration_s)
        + opt_u
        + edge_gaps(truth, opt_u, silence or [], cfg.edge_gap_s)
    )
    blocks = _blocks(truth_u, opt_u)

    score = EpisodeScore(
        episode_id,
        duration_s,
        expected,
        outcome,
        seconds_scored=True,
        truth_ad_s=total(truth_u),
        pred_ad_s=total(pred_u),
        content_cut_s=total(subtract(subtract(pred_u, truth_u), zones)),
        missed_ad_s=total(subtract(subtract(truth_u, pred_u), zones)),
        optional_s=total(opt_u),
        optional_cut_s=total(intersect(pred_u, opt_u)),
        truth_blocks=len(blocks),
    )
    for ts, te in blocks:
        overlaps = [(min(te, pe) - max(ts, ps), (ps, pe)) for ps, pe in pred_u if pe > ts and ps < te]
        block_truth = intersect([(ts, te)], truth_u)
        covered = total(intersect(block_truth, pred_u))
        if overlaps and covered >= BLOCK_COVER * total(block_truth):
            score.found_blocks += 1
            _, (ps, pe) = max(overlaps)
            start_err = -total(subtract([(ps, ts)], opt_u)) if ps < ts else ps - ts
            end_err = total(subtract([(te, pe)], opt_u)) if pe > te else pe - te
            score.boundary_errors.append((round(start_err, 2), round(end_err, 2)))
    for ps, pe in pred_u:
        scored = subtract([(ps, pe)], opt_u)
        if total(scored) < pe - ps and not subtract(scored, zones):
            continue  # a cut of optional audio only: not an error of any kind
        if total(intersect(scored, truth_u)) < BLOCK_COVER * total(scored):
            score.spurious_blocks += 1
    return score


@dataclass
class Aggregate:
    episodes: int
    scored_hours: float
    content_cut_per_h: float
    missed_ad_per_h: float
    weighted_error_per_h: float
    ad_recall: float  # share of truth ad seconds removed
    content_cut_pct: float  # share of non-ad seconds removed
    optional_cut: float  # share of optional seconds removed. Informational.
    blunders: int
    block_recall: float
    spurious_blocks: int
    median_abs_start_err_s: float | None
    median_abs_end_err_s: float | None
    outcome_accuracy: float
    reviews: int
    errors: int


def aggregate(scores: list[EpisodeScore], cfg: ScoreConfig) -> Aggregate:
    scored = [s for s in scores if s.seconds_scored]
    hours = sum(s.duration_s for s in scored) / 3600 or float("nan")
    content_cut = sum(s.content_cut_s for s in scored)
    missed = sum(s.missed_ad_s for s in scored)
    truth_ad = sum(s.truth_ad_s for s in scored)
    content_total = sum(s.duration_s - s.truth_ad_s - s.optional_s for s in scored)
    optional_total = sum(s.optional_s for s in scored)
    truth_blocks = sum(s.truth_blocks for s in scored)
    start_errs = [abs(e[0]) for s in scored for e in s.boundary_errors]
    end_errs = [abs(e[1]) for s in scored for e in s.boundary_errors]
    return Aggregate(
        episodes=len(scores),
        scored_hours=round(hours, 2),
        content_cut_per_h=content_cut / hours,
        missed_ad_per_h=missed / hours,
        weighted_error_per_h=(missed + cfg.content_weight * content_cut) / hours,
        ad_recall=1 - missed / truth_ad if truth_ad else float("nan"),
        content_cut_pct=100 * content_cut / content_total if content_total else float("nan"),
        optional_cut=sum(s.optional_cut_s for s in scored) / optional_total if optional_total else float("nan"),
        blunders=sum(1 for s in scored if s.blunder(cfg)),
        block_recall=sum(s.found_blocks for s in scored) / truth_blocks if truth_blocks else float("nan"),
        spurious_blocks=sum(s.spurious_blocks for s in scored),
        median_abs_start_err_s=statistics.median(start_errs) if start_errs else None,
        median_abs_end_err_s=statistics.median(end_errs) if end_errs else None,
        outcome_accuracy=sum(1 for s in scores if s.outcome_ok) / len(scores) if scores else float("nan"),
        reviews=sum(1 for s in scores if s.outcome == "review"),
        errors=sum(1 for s in scores if s.outcome == "error"),
    )
