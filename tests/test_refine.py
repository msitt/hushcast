from hushcast.detection.refine import (
    WordGap,
    collect_words,
    count_moved,
    refine_boundaries,
    word_gaps,
)
from hushcast.detection.segments import AdSegment
from hushcast.transcription.base import Transcript, TranscriptSegment, Word


def seg(start, end, category="ad", confidence=0.9, reason=""):
    return AdSegment(start=start, end=end, category=category, confidence=confidence, reason=reason)


def make_transcript_with_words():
    words = [
        Word(text="a", start=0.0, end=0.4),
        Word(text="b", start=0.5, end=0.9),  # gap 0.4-0.5 (0.1s)
        Word(text="c", start=2.0, end=2.4),  # gap 0.9-2.0 (1.1s)
        Word(text="d", start=2.5, end=2.9),
    ]
    return Transcript(
        language="en", duration=10.0,
        segments=[TranscriptSegment(text="a b", start=0.0, end=0.9, words=words[:2]),
                  TranscriptSegment(text="c d", start=2.0, end=2.9, words=words[2:])],
    )


class TestCollect:
    def test_collect_words_flattens_sorted(self):
        words = collect_words(make_transcript_with_words())
        assert [w.text for w in words] == ["a", "b", "c", "d"]

    def test_no_word_timestamps(self):
        t = Transcript(language=None, duration=10.0, segments=[TranscriptSegment(text="x", start=0, end=5)])
        assert collect_words(t) == []

    def test_word_gaps(self):
        gaps = word_gaps(collect_words(make_transcript_with_words()))
        assert gaps == [WordGap(0.4, 0.5), WordGap(0.9, 2.0), WordGap(2.4, 2.5)]

    def test_word_gaps_with_duration_adds_edge_gaps(self):
        words = [Word(text="a", start=0.3, end=0.9), Word(text="b", start=1.2, end=2.9)]
        gaps = word_gaps(words, 10.0)
        assert gaps == [WordGap(0.0, 0.3), WordGap(0.9, 1.2), WordGap(2.9, 10.0)]

    def test_word_gaps_no_degenerate_edge_gaps(self):
        # first word at 0 and last word ending at duration produce no edge gaps
        words = [Word(text="a", start=0.0, end=4.0), Word(text="b", start=5.0, end=10.0)]
        assert word_gaps(words, 10.0) == [WordGap(4.0, 5.0)]

    def test_word_gaps_empty_words(self):
        assert word_gaps([], 10.0) == []


class TestRefine:
    GAPS = [
        WordGap(9.8, 10.0),    # 0.2s wide, mid 9.9
        WordGap(11.0, 12.0),   # 1.0s wide, mid 11.5
        WordGap(29.9, 30.3),   # 0.4s wide, mid 30.1
    ]

    def test_identity_without_gaps(self):
        s = [seg(10, 30)]
        assert refine_boundaries(s, [], window_s=4, min_gap_s=0.15) == s

    def test_moves_to_widest_gap_in_window(self):
        out = refine_boundaries([seg(10, 30)], self.GAPS, window_s=4, min_gap_s=0.15)
        # widest gap near 10 within window is 11.0-12.0 -> midpoint 11.5
        assert out[0].start == 11.5
        assert out[0].end == 30.1

    def test_prefers_nearest_on_tie(self):
        gaps = [WordGap(8.0, 9.0), WordGap(11.0, 12.0)]  # equal width; 11.5 is nearer to 12 than 8.5
        out = refine_boundaries([seg(12, 30)], gaps, window_s=4, min_gap_s=0.15)
        assert out[0].start == 11.5

    def test_respects_min_gap(self):
        out = refine_boundaries([seg(10, 30)], [WordGap(9.95, 10.0)], window_s=4, min_gap_s=0.15)
        assert out[0].start == 10  # only gap is too narrow

    def test_respects_window(self):
        out = refine_boundaries([seg(10, 30)], [WordGap(20.0, 21.0)], window_s=4, min_gap_s=0.15)
        assert (out[0].start, out[0].end) == (10, 30)  # gap midpoint 20.5 too far from both ends

    def test_never_inverts_segment(self):
        # a single gap near both ends of a short segment would invert it
        out = refine_boundaries([seg(10, 11)], [WordGap(11.5, 12.5)], window_s=4, min_gap_s=0.15)
        assert (out[0].start, out[0].end) == (10, 11)

    def test_never_overlaps_neighbor(self):
        gaps = [WordGap(19.0, 21.0)]  # midpoint 20.0 sits between the segments
        out = refine_boundaries([seg(10, 19.5), seg(20.5, 30)], gaps, window_s=4, min_gap_s=0.15)
        # first end moves to 20.0, second start may not cross into it and 20.0 <= 20.0 is fine
        assert out[0].end == 20.0
        assert out[1].start >= out[0].end

    def test_distance_penalty_prefers_near_gap_over_wider_far_one(self):
        # Regression: episode with LLM start boundary 62.7 right at a sentence
        # break (0.62s gap) and a slightly wider mid-sentence breath (0.76s)
        # 2.4s earlier. Widest-in-window used to pick the far gap and clip
        # "...if we have the will to do it" out of real content.
        gaps = [WordGap(59.875, 60.636), WordGap(62.116, 62.736)]
        out = refine_boundaries([seg(62.7, 75.3)], gaps, window_s=4, min_gap_s=0.15)
        assert out[0].start == (62.116 + 62.736) / 2

    def test_wider_far_gap_still_wins_over_marginal_near_one(self):
        # A barely-qualifying comma pause at the boundary shouldn't beat a
        # clean production gap 1.5s away.
        gaps = [WordGap(9.93, 10.09), WordGap(11.1, 11.9)]  # 0.16s @10.01 vs 0.8s @11.5
        out = refine_boundaries([seg(10, 30)], gaps, window_s=4, min_gap_s=0.15)
        assert out[0].start == 11.5

    def test_boundary_at_edge_of_wide_silence_stays_out_of_content(self):
        # Regression: an LLM start boundary right where the ad's first word
        # begins, after a ~9s production silence. Midpoint-distance scoring
        # put that silence's midpoint outside the 2s window, so the only
        # candidate was a breath pause ~1.2s into the ad and the cut left the
        # ad's opening words in the audio. Edge distance is 0, so the wide
        # silence wins, the target is its midpoint clamped to window_s
        # before the boundary, still inside the silence.
        gaps = [WordGap(169.8, 179.0), WordGap(180.099, 180.249)]
        out = refine_boundaries([seg(179.0, 216.0)], gaps, window_s=2, min_gap_s=0.15)
        assert out[0].start == 177.0  # boundary - window_s, within the silence
        assert out[0].start <= 179.0  # never starts the cut after the ad begins

    def test_wide_gap_target_clamped_to_window(self):
        # A wide gap whose midpoint is far away may win, but the cut point
        # must stay within window_s of the original boundary.
        out = refine_boundaries([seg(10, 30)], [WordGap(0.0, 9.9)], window_s=4, min_gap_s=0.15)
        assert out[0].start == 6.0  # clamp(4.95, 10-4, 10+4), not the raw midpoint

    def test_outward_moves_penalized_double(self):
        # Equal-width gaps equidistant on both sides of a start boundary:
        # expanding the cut (earlier) risks clipping content, so the inward
        # (later) gap wins.
        gaps = [WordGap(8.5, 9.0), WordGap(11.0, 11.5)]  # mids 8.75 and 11.25, boundary 10
        out = refine_boundaries([seg(10, 30)], gaps, window_s=4, min_gap_s=0.15)
        assert out[0].start == 11.25

    def test_preroll_ad_start_stays_before_first_word(self):
        # Regression: pre-roll ad whose first word starts at
        # 0.3. With no gap representing the pre-roll silence, the only
        # candidate near the start boundary was a breath pause ~1s into the
        # ad, and the cut left the ad's first words in the audio. The virtual leading
        # gap [0, 0.3] scores at zero distance and wins. The cut lands at its
        # midpoint, inside the pre-roll silence.
        gaps = [WordGap(0.0, 0.3), WordGap(0.95, 1.19)]
        out = refine_boundaries([seg(0.3, 29.8)], gaps, window_s=2, min_gap_s=0.15)
        assert out[0].start == 0.15

    def test_edge_extended_start_not_dragged_into_ad(self):
        # Same episode after extend_to_edges pulled the start to 0.0:
        # refinement must not undo the extension by moving into the ad.
        gaps = [WordGap(0.0, 0.3), WordGap(0.95, 1.19)]
        out = refine_boundaries([seg(0.0, 29.8)], gaps, window_s=2, min_gap_s=0.15)
        assert out[0].start <= 0.3  # never after the ad's first word

    def test_tail_ad_end_stays_after_last_word(self):
        # Mirror case at the episode tail: the virtual trailing gap keeps the
        # end boundary inside the outro silence instead of pulling it back
        # before the ad's last words.
        gaps = [WordGap(95.4, 95.7), WordGap(99.0, 100.0)]  # last word ends at 99.0
        out = refine_boundaries([seg(80.0, 100.0)], gaps, window_s=2, min_gap_s=0.15)
        assert out[0].end >= 99.0

    def test_outro_ad_end_not_dragged_inward_when_speech_runs_to_audio_end(self):
        # Regression: outro self-promo running to the end of the
        # episode. The transcript-derived duration equals the last word's end
        # (2200.16), so word_gaps built with it had no trailing virtual gap and
        # refinement pulled the end boundary back to a breath pause mid-line
        # (~2198.2), leaving "...and wherever you listen to podcasts" in the
        # audio. detect.py now passes the probed audio duration (2200.998),
        # which restores the trailing gap.
        words = [
            Word(text="our", start=2197.258, end=2197.678),
            Word(text="app", start=2197.858, end=2198.018),
            Word(text="and", start=2198.399, end=2198.479),
            Word(text="wherever", start=2198.519, end=2198.939),
            Word(text="you", start=2198.979, end=2199.099),
            Word(text="listen", start=2199.139, end=2199.419),
            Word(text="to", start=2199.439, end=2199.519),
            Word(text="podcasts.", start=2199.559, end=2200.16),
        ]
        # transcript-derived duration == last word end: no trailing gap exists
        assert word_gaps(words, 2200.16)[-1].end < 2200.16
        gaps = word_gaps(words, 2200.998)
        assert gaps[-1] == WordGap(2200.16, 2200.998)
        out = refine_boundaries([seg(2176.9, 2200.2)], gaps, window_s=2, min_gap_s=0.15)
        assert out[0].end >= 2200.16  # cut stays after the ad's last word

    def test_zero_window_never_moves(self):
        out = refine_boundaries([seg(10, 30)], self.GAPS, window_s=0, min_gap_s=0.15)
        assert (out[0].start, out[0].end) == (10, 30)

    def test_count_moved(self):
        before = [seg(10, 30)]
        after = refine_boundaries(before, self.GAPS, window_s=4, min_gap_s=0.15)
        assert count_moved(before, after) == 2
        assert count_moved(before, before) == 0
