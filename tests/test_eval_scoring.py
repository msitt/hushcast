import math

import pytest

from hushcast.evals.scoring import (
    EDGE_SILENCE_MAX_S,
    ScoreConfig,
    aggregate,
    score_episode,
    silence_intervals,
)

CFG = ScoreConfig(collar_s=1.0, content_weight=3.0, blunder_s=5.0)


def score(truth, predicted, *, outcome="cut", promo=False, duration=3600.0, cfg=CFG, optional=None, silence=None):
    return score_episode(
        episode_id=1, duration_s=duration, truth=truth, truth_promo=promo,
        predicted=predicted, outcome=outcome, cfg=cfg, optional=optional, silence=silence,
    )


def test_perfect_prediction():
    s = score([(100, 160)], [(100, 160)])
    assert s.content_cut_s == 0 and s.missed_ad_s == 0
    assert s.found_blocks == 1 and s.spurious_blocks == 0
    assert s.boundary_errors == [(0, 0)]
    assert s.outcome_ok


def test_collar_forgives_small_boundary_slop():
    s = score([(100, 160)], [(99.5, 160.8)])
    assert s.content_cut_s == 0
    assert s.missed_ad_s == 0
    assert s.boundary_errors == [(-0.5, 0.8)]


def test_content_cut_beyond_collar_is_counted_and_weighted():
    s = score([(100, 160)], [(90, 170)])
    assert s.content_cut_s == pytest.approx(18)  # 9 s each side outside the 1 s collar
    assert s.missed_ad_s == 0
    assert s.weighted_error_s(CFG) == pytest.approx(54)
    assert s.blunder(CFG)


def test_missed_ad():
    s = score([(100, 160), (1000, 1030)], [(100, 160)])
    assert s.missed_ad_s == pytest.approx(28)  # 30 s minus the collar inside each edge
    assert s.truth_blocks == 2 and s.found_blocks == 1
    assert not s.blunder(CFG)


def test_spurious_block():
    s = score([(100, 160)], [(100, 160), (2000, 2030)])
    assert s.spurious_blocks == 1
    assert s.content_cut_s == pytest.approx(30)


def test_no_ads_episode():
    s = score([], [])
    assert s.content_cut_s == 0 and s.missed_ad_s == 0 and s.outcome_ok


def test_error_and_promo_predictions_cut_nothing():
    for outcome in ("error", "promo"):
        s = score([(100, 160)], [(100, 160)], outcome=outcome)
        assert s.missed_ad_s == pytest.approx(58)
        assert s.content_cut_s == 0
        assert not s.outcome_ok


def test_review_scores_its_segments_but_is_not_a_correct_outcome():
    s = score([(100, 160)], [(100, 160)], outcome="review")
    assert s.missed_ad_s == 0
    assert not s.outcome_ok


def test_promo_truth_is_outcome_only():
    assert score([(0, 300)], [(0, 300)], outcome="promo", promo=True).outcome_ok
    s = score([(0, 300)], [(0, 300)], outcome="cut", promo=True)
    assert not s.outcome_ok and not s.seconds_scored


def test_aggregate_per_hour():
    a = score([(100, 160)], [(90, 160)], duration=1800)  # 9 s content cut
    b = score([(100, 160)], [], duration=1800)  # 58 s missed
    p = score([(0, 300)], [(0, 300)], outcome="promo", promo=True)
    agg = aggregate([a, b, p], CFG)
    assert agg.episodes == 3
    assert agg.scored_hours == 1.0
    assert agg.content_cut_per_h == pytest.approx(9)
    assert agg.missed_ad_per_h == pytest.approx(58)
    assert agg.weighted_error_per_h == pytest.approx(58 + 27)
    assert agg.blunders == 1
    assert agg.block_recall == 0.5
    assert agg.outcome_accuracy == 1.0
    assert agg.ad_recall == pytest.approx(1 - 58 / 120)


def test_aggregate_empty():
    agg = aggregate([], CFG)
    assert agg.episodes == 0
    assert math.isnan(agg.outcome_accuracy)


def test_optional_range_scores_the_same_cut_or_kept():
    truth, opt = [(100, 160)], [(85, 100)]  # a teaser right before the break
    cut = score(truth, [(85, 160)], optional=opt)
    kept = score(truth, [(100, 160)], optional=opt)
    for s in (cut, kept):
        assert s.content_cut_s == 0 and s.missed_ad_s == 0
        assert s.found_blocks == 1 and s.spurious_blocks == 0
        assert s.boundary_errors == [(0, 0)]
    assert cut.optional_cut_s == 15 and kept.optional_cut_s == 0
    assert cut.optional_s == kept.optional_s == 15


def test_cutting_past_an_optional_range_still_counts():
    s = score([(100, 160)], [(75, 160)], optional=[(85, 100)])
    assert s.content_cut_s == pytest.approx(9)  # 75 to 85, minus the collar at the optional edge
    assert s.boundary_errors == [(-10, 0)]


def test_cut_of_only_optional_audio_is_not_spurious():
    s = score([(100, 160)], [(100, 160), (2000, 2008)], optional=[(2000, 2008)])
    assert s.spurious_blocks == 0 and s.content_cut_s == 0
    assert s.truth_blocks == 1


def test_optional_range_inside_a_break_keeps_it_one_block():
    truth, opt = [(100, 120), (125, 160)], [(120, 125)]
    whole = score(truth, [(100, 160)], optional=opt)
    split = score(truth, [(100, 120), (125, 160)], optional=opt)
    for s in (whole, split):
        assert s.truth_blocks == 1 and s.found_blocks == 1
        assert s.content_cut_s == 0 and s.missed_ad_s == 0
    assert whole.boundary_errors == [(0, 0)]


def test_optional_overrides_truth_it_overlaps():
    s = score([(100, 160)], [(110, 160)], optional=[(100, 110)])
    assert s.missed_ad_s == 0 and s.truth_ad_s == 50


def test_aggregate_reports_optional_share_cut():
    a = aggregate(
        [score([(100, 160)], [(90, 160)], optional=[(90, 100)]), score([(100, 160)], [(100, 160)], optional=[(90, 100)])],
        CFG,
    )
    assert a.optional_cut == pytest.approx(0.5)
    assert a.weighted_error_per_h == 0
    assert math.isnan(aggregate([score([(100, 160)], [(100, 160)])], CFG).optional_cut)


def test_silence_at_a_boundary_is_not_scored():
    # the ad ends at 160, then 3 s of silence before the show resumes
    kept = score([(100, 160)], [(100, 157)], silence=[(157, 160)])
    assert kept.missed_ad_s == 0
    cut = score([(100, 157)], [(100, 160)], silence=[(157, 160)])
    assert cut.content_cut_s == 0


def test_speech_past_the_edge_silence_still_counts():
    s = score([(100, 160)], [(100, 165)], silence=[(160, 163)])
    assert s.content_cut_s == pytest.approx(2)  # 163-165 is show speech


def test_long_or_distant_silence_is_still_scored():
    long = EDGE_SILENCE_MAX_S + 1
    s = score([(100, 160)], [(100, 160 + long)], silence=[(160, 160 + long)])
    assert s.content_cut_s == pytest.approx(long - 1)  # only the collar is forgiven
    far = score([(100, 160)], [(100, 166)], silence=[(163, 166)])
    assert far.content_cut_s == pytest.approx(5)  # 161-166, the silence doesn't touch the edge


def test_silence_intervals_from_lines_words_and_cues():
    transcript = {"segments": [
        {"start": 0.0, "end": 4.0, "words": [{"start": 0.0, "end": 1.0}, {"start": 1.5, "end": 4.0}]},
        {"start": 6.0, "end": 9.0, "words": []},
    ]}
    cues = [{"start": 20.0, "end": 22.0, "kind": "silence"}, {"start": 30.0, "end": 40.0, "kind": "music"}]
    assert silence_intervals(transcript, cues) == [(1.0, 1.5), (4.0, 6.0), (20.0, 22.0)]
    assert silence_intervals(None, None) == []
