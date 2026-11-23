import math

import pytest

from hushcast.evals.scoring import ScoreConfig, aggregate, score_episode

CFG = ScoreConfig(collar_s=1.0, content_weight=3.0, blunder_s=5.0)


def score(truth, predicted, *, outcome="cut", promo=False, duration=3600.0, cfg=CFG):
    return score_episode(
        episode_id=1, duration_s=duration, truth=truth, truth_promo=promo,
        predicted=predicted, outcome=outcome, cfg=cfg,
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
