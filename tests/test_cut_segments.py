from hushcast.evals.truth import SegmentRow
from hushcast.pipeline.steps.cut import cut_segments


def spans(rows):
    return [(s.start, s.end, s.category) for s in cut_segments(rows)]


def test_detected_segments_are_cut():
    rows = [SegmentRow(10.0, 20.0, "sponsor", False, "llm")]
    assert spans(rows) == [(10.0, 20.0, "sponsor")]


def test_trim_inside_a_block_splits_it():
    rows = [
        SegmentRow(0.0, 60.0, "ad", False, "llm"),
        SegmentRow(20.0, 30.0, "ad", True, "manual"),
    ]
    assert spans(rows) == [(0.0, 20.0, "ad"), (30.0, 60.0, "ad")]


def test_trim_at_the_edge_shortens_the_block():
    # the episode 2519 shape: an ad, then show content flagged with it
    rows = [
        SegmentRow(0.0, 181.3, "self_promo", False, "llm"),
        SegmentRow(26.7, 181.3, "self_promo", True, "manual"),
    ]
    assert spans(rows) == [(0.0, 26.7, "self_promo")]


def test_kept_detection_is_not_cut_and_optional_ranges_change_nothing():
    rows = [
        SegmentRow(0.0, 10.0, "ad", True, "llm"),
        SegmentRow(40.0, 50.0, "ad", False, "llm"),
        SegmentRow(45.0, 50.0, "optional", True, "optional"),
    ]
    assert spans(rows) == [(40.0, 50.0, "ad")]


def test_manual_missed_ad_is_cut():
    rows = [SegmentRow(30.0, 45.0, "ad", False, "manual")]
    assert spans(rows) == [(30.0, 45.0, "ad")]


def test_promo_episode_cuts_nothing():
    rows = [SegmentRow(0.0, 100.0, "self_promo", True, "promo")]
    assert spans(rows) == []
