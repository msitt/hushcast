from hushcast.detection.refine import WordGap
from hushcast.detection.segments import (
    AdSegment,
    DetectionRejected,
    bridge_over_cues,
    check_removed_fraction,
    clamp,
    extend_to_edges,
    filter_thresholds,
    keep_ranges,
    merge,
    postprocess,
    snap_to_boundaries,
)
import pytest


def seg(start, end, category="ad", confidence=0.9, reason=""):
    return AdSegment(start=start, end=end, category=category, confidence=confidence, reason=reason)


class TestClamp:
    def test_clamps_to_duration(self):
        out = clamp([seg(-5, 20), seg(100, 500)], duration=300)
        assert out == [seg(0, 20), seg(100, 300)]

    def test_drops_inverted_and_out_of_range(self):
        assert clamp([seg(50, 40), seg(400, 500)], duration=300) == []


class TestSnap:
    BOUNDS = [0.0, 10.0, 20.0, 30.0, 40.0, 100.0]

    def test_snaps_within_tolerance(self):
        out = snap_to_boundaries([seg(9.2, 31.1)], self.BOUNDS, tolerance_s=5)
        assert out == [seg(10.0, 30.0)]

    def test_discards_hallucinated_timestamps(self):
        assert snap_to_boundaries([seg(60.0, 75.0)], self.BOUNDS, tolerance_s=5) == []

    def test_discards_collapsed_segment(self):
        # both ends snap to the same boundary -> zero length -> discarded
        assert snap_to_boundaries([seg(9.5, 10.5)], self.BOUNDS, tolerance_s=5) == []

    def test_no_boundaries_passthrough(self):
        assert snap_to_boundaries([seg(1, 2)], [], tolerance_s=5) == [seg(1, 2)]


class TestThresholds:
    def test_confidence_and_duration(self):
        segments = [seg(0, 30, confidence=0.5), seg(0, 5, confidence=0.9), seg(50, 90, confidence=0.9)]
        assert filter_thresholds(segments, min_confidence=0.6, min_duration_s=8) == [seg(50, 90, confidence=0.9)]


class TestMerge:
    def test_merges_overlap_and_small_gaps(self):
        out = merge([seg(0, 30), seg(28, 60), seg(63, 90)], gap_s=5)
        assert len(out) == 1
        assert (out[0].start, out[0].end) == (0, 90)

    def test_respects_large_gaps(self):
        out = merge([seg(0, 30), seg(100, 130)], gap_s=5)
        assert len(out) == 2

    def test_dedupes_chunk_overlap_duplicates(self):
        out = merge([seg(300, 360), seg(300, 360)], gap_s=5)
        assert out == [seg(300, 360)]

    def test_keeps_max_confidence(self):
        out = merge([seg(0, 30, confidence=0.7), seg(20, 60, confidence=0.95)], gap_s=5)
        assert out[0].confidence == 0.95

    def test_category_prefers_longer_contributor(self):
        out = merge([seg(0, 10, category="self_promo"), seg(8, 60, category="sponsor")], gap_s=5)
        assert out[0].category == "sponsor"


class TestBridgeOverCues:
    def test_bridges_fully_covered_gap(self):
        cues = [(30.0, 42.0)]  # music covering the whole gap
        out = bridge_over_cues([seg(0, 30), seg(42, 60)], cues, max_bridge_gap_s=15)
        assert len(out) == 1
        assert (out[0].start, out[0].end) == (0, 60)

    def test_slack_absorbs_snap_rounding(self):
        cues = [(30.8, 41.5)]  # 1.3s of the 12s gap uncovered -> over the 1s slack
        out = bridge_over_cues([seg(0, 30), seg(42, 60)], cues, max_bridge_gap_s=15)
        assert len(out) == 2
        cues = [(30.4, 41.8)]  # 0.6s uncovered -> within slack
        out = bridge_over_cues([seg(0, 30), seg(42, 60)], cues, max_bridge_gap_s=15)
        assert len(out) == 1

    def test_gap_over_cap_not_bridged(self):
        cues = [(30.0, 60.0)]
        out = bridge_over_cues([seg(0, 30), seg(60, 90)], cues, max_bridge_gap_s=15)
        assert len(out) == 2

    def test_chained_bridges(self):
        cues = [(10.0, 15.0), (25.0, 30.0)]
        out = bridge_over_cues([seg(0, 10), seg(15, 25), seg(30, 40)], cues, max_bridge_gap_s=10)
        assert len(out) == 1
        assert (out[0].start, out[0].end) == (0, 40)

    def test_uncovered_gap_not_bridged(self):
        out = bridge_over_cues([seg(0, 30), seg(35, 60)], [(100.0, 110.0)], max_bridge_gap_s=15)
        assert len(out) == 2

    def test_no_cues_passthrough(self):
        s = [seg(0, 30), seg(35, 60)]
        assert bridge_over_cues(s, [], max_bridge_gap_s=15) == s


class TestExtendToEdges:
    def test_extends_over_leading_sting(self):
        out = extend_to_edges([seg(8, 60)], [(0.0, 8.5)], duration=600, max_edge_extension_s=20)
        assert out[0].start == 0.0

    def test_extends_over_trailing_sting(self):
        out = extend_to_edges([seg(540, 590)], [(589.5, 600.0)], duration=600, max_edge_extension_s=20)
        assert out[0].end == 600.0

    def test_cap_respected(self):
        out = extend_to_edges([seg(30, 60)], [(0.0, 30.0)], duration=600, max_edge_extension_s=20)
        assert out[0].start == 30

    def test_uncovered_lead_not_extended(self):
        # 8s before the ad but no cue coverage: real content, keep it
        out = extend_to_edges([seg(8, 60)], [(100.0, 110.0)], duration=600, max_edge_extension_s=20)
        assert out[0].start == 8


class TestCircuitBreaker:
    def test_rejects_over_limit(self):
        with pytest.raises(DetectionRejected):
            check_removed_fraction([seg(0, 200)], duration=300, max_removed_pct=50)

    def test_allows_under_limit(self):
        check_removed_fraction([seg(0, 100)], duration=300, max_removed_pct=50)


class TestKeepRanges:
    def test_complement(self):
        ranges = keep_ranges([seg(60, 120), seg(300, 360)], duration=600)
        assert ranges == [(0.0, 60), (120, 300), (360, 600)]

    def test_ad_at_start_and_end(self):
        ranges = keep_ranges([seg(0, 60), seg(540, 600)], duration=600)
        assert ranges == [(60, 540)]

    def test_no_segments(self):
        assert keep_ranges([], duration=600) == [(0.0, 600)]


class TestPostprocess:
    def test_end_to_end(self):
        boundaries = [float(x) for x in range(0, 1801, 10)]
        raw = [
            seg(61.0, 122.0, confidence=0.9),     # snaps to 60-120
            seg(118.0, 181.0, confidence=0.85),   # merges with previous
            seg(900.0, 903.0, confidence=0.9),    # too short after snap (900-900) -> dropped
            seg(1500.4, 1567.0, confidence=0.4),  # below confidence -> dropped
        ]
        out = postprocess(
            raw, duration=1800, boundaries=boundaries, snap_tolerance_s=15,
            min_confidence=0.6, min_duration_s=8, merge_gap_s=5, max_removed_pct=50,
        )
        assert len(out) == 1
        assert (out[0].start, out[0].end) == (60.0, 180.0)

    def test_bridging_runs_before_circuit_breaker(self):
        # two 60s ads bridged across a 120s music gap -> 240s of a 600s episode
        # = 40%: fine at 50%, but the breaker must judge the bridged total
        boundaries = [float(x) for x in range(0, 601, 10)]
        raw = [seg(0.0, 60.0), seg(180.0, 240.0)]
        kwargs = dict(
            duration=600, boundaries=boundaries, snap_tolerance_s=15,
            min_confidence=0.6, min_duration_s=8, merge_gap_s=5,
            cue_intervals=[(60.0, 180.0)], bridge_max_gap_s=150,
        )
        out = postprocess(raw, max_removed_pct=50, **kwargs)
        assert len(out) == 1
        assert (out[0].start, out[0].end) == (0.0, 240.0)
        with pytest.raises(DetectionRejected):
            postprocess(raw, max_removed_pct=30, **kwargs)

    def test_refinement_applies_after_merge(self):
        boundaries = [0.0, 60.0, 120.0]
        raw = [seg(0.0, 60.0)]
        out = postprocess(
            raw, duration=120, boundaries=boundaries, snap_tolerance_s=15,
            min_confidence=0.6, min_duration_s=8, merge_gap_s=5, max_removed_pct=90,
            refine_gaps=[WordGap(61.0, 62.0)], refine_window_s=4, refine_min_gap_s=0.15,
        )
        assert out[0].end == 61.5
