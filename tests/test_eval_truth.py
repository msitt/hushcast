from hushcast.evals.intervals import intersect, subtract, total, union
from hushcast.evals.truth import SegmentRow, TruthSpan, effective_truth


def row(start, end, *, kept=False, source="llm", category="ad"):
    return SegmentRow(start, end, category, kept, source)


def test_interval_ops():
    assert union([(5, 7), (0, 2), (1, 3), (3, 3)]) == [(0, 3), (5, 7)]
    assert subtract([(0, 10)], [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]
    assert subtract([(0, 10)], [(0, 10)]) == []
    assert intersect([(0, 5), (8, 12)], [(4, 9)]) == [(4, 5), (8, 9)]
    assert total([(0, 2), (1, 3)]) == 3


def test_uncorrected_detection_is_the_truth():
    t = effective_truth([row(10, 40), row(100, 130, category="sponsor")])
    assert t.promo is False
    assert t.spans == [TruthSpan(10, 40, "ad"), TruthSpan(100, 130, "sponsor")]


def test_not_an_ad_drops_the_segment():
    t = effective_truth([row(10, 40, kept=True), row(100, 130)])
    assert t.spans == [TruthSpan(100, 130, "ad")]


def test_missed_ad_and_extension_are_added():
    t = effective_truth([row(10, 40), row(38, 50, source="manual"), row(200, 230, source="manual")])
    assert t.spans == [TruthSpan(10, 50, "ad"), TruthSpan(200, 230, "ad")]


def test_trim_splits_a_block():
    t = effective_truth([row(10, 60), row(30, 35, kept=True, source="manual")])
    assert t.spans == [TruthSpan(10, 30, "ad"), TruthSpan(35, 60, "ad")]


def test_promo_episode():
    t = effective_truth([row(0, 290, kept=True, source="promo")])
    assert t.promo is True
    assert t.spans == [TruthSpan(0, 290, "ad")]


def optional(start, end):
    return row(start, end, kept=True, source="optional", category="optional")


def test_optional_range_is_not_truth():
    t = effective_truth([row(10, 40), optional(200, 210), optional(205, 215)])
    assert t.spans == [TruthSpan(10, 40, "ad")]
    assert t.optional == [(200, 215)]


def test_optional_range_takes_its_part_out_of_a_detected_block():
    t = effective_truth([row(10, 60), optional(5, 20), optional(30, 35)])
    assert t.spans == [TruthSpan(20, 30, "ad"), TruthSpan(35, 60, "ad")]
    assert t.optional == [(5, 20), (30, 35)]


def test_optional_range_also_overrides_a_manual_ad():
    t = effective_truth([row(100, 130, source="manual"), optional(100, 110)])
    assert t.spans == [TruthSpan(110, 130, "ad")]


def test_no_segments_means_no_ads():
    assert effective_truth([]).spans == []
