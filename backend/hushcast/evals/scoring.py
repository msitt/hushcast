"""Score predicted ad cuts against verified truth.

The two raw error measures, in seconds of audio:
- content cut: non-ad audio the prediction removes (the worse error)
- ad missed: ad audio the prediction leaves in

weighted error = ad missed + content_weight * content cut, so a single number
ranks models while the raw pair stays visible. A collar around every truth
boundary is ignored for both: labels and transcript line edges are only
accurate to a second or two, and scoring that slack rewards luck.

Promo-only episodes (truth or prediction) are scored on the outcome only.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .intervals import Interval, intersect, subtract, total, union

# A truth block counts as found when the prediction covers this share of it,
# and a predicted block as spurious when truth covers less than this share.
BLOCK_COVER = 0.5


@dataclass(frozen=True)
class ScoreConfig:
    collar_s: float = 1.0
    content_weight: float = 3.0
    blunder_s: float = 5.0  # an episode with more content cut than this is a blunder


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
    truth_blocks: int = 0
    found_blocks: int = 0
    spurious_blocks: int = 0
    # per truth block found: (pred start - truth start, pred end - truth end).
    # A negative start or positive end error cuts content.
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


def score_episode(
    *,
    episode_id: int,
    duration_s: float,
    truth: list[Interval],
    truth_promo: bool,
    predicted: list[Interval],
    outcome: str,
    cfg: ScoreConfig,
) -> EpisodeScore:
    expected = "promo" if truth_promo else "cut"
    if truth_promo:
        return EpisodeScore(episode_id, duration_s, expected, outcome, seconds_scored=False)

    truth_u = union(truth)
    # promo and error outcomes cut nothing
    pred_u = union(predicted) if outcome in ("cut", "review") else []
    zones = _collar(truth_u, cfg.collar_s, duration_s)

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
        truth_blocks=len(truth_u),
    )
    for ts, te in truth_u:
        overlaps = [(min(te, pe) - max(ts, ps), (ps, pe)) for ps, pe in pred_u if pe > ts and ps < te]
        covered = total(intersect([(ts, te)], pred_u))
        if overlaps and covered >= BLOCK_COVER * (te - ts):
            score.found_blocks += 1
            _, (ps, pe) = max(overlaps)
            score.boundary_errors.append((round(ps - ts, 2), round(pe - te, 2)))
    for ps, pe in pred_u:
        if total(intersect([(ps, pe)], truth_u)) < BLOCK_COVER * (pe - ps):
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
    content_total = sum(s.duration_s - s.truth_ad_s for s in scored)
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
        blunders=sum(1 for s in scored if s.blunder(cfg)),
        block_recall=sum(s.found_blocks for s in scored) / truth_blocks if truth_blocks else float("nan"),
        spurious_blocks=sum(s.spurious_blocks for s in scored),
        median_abs_start_err_s=statistics.median(start_errs) if start_errs else None,
        median_abs_end_err_s=statistics.median(end_errs) if end_errs else None,
        outcome_accuracy=sum(1 for s in scores if s.outcome_ok) / len(scores) if scores else float("nan"),
        reviews=sum(1 for s in scores if s.outcome == "review"),
        errors=sum(1 for s in scores if s.outcome == "error"),
    )
