from hushcast.transcription.base import Transcript, TranscriptSegment, Word


def _transcript(language, segments):
    return Transcript(language=language, duration=segments[-1].end if segments else 0.0, segments=segments)


def test_concat_shifts_timestamps_by_offset():
    part_a = _transcript("en", [
        TranscriptSegment(text="hello", start=0.0, end=5.0, words=[Word(text="hello", start=0.0, end=1.0)]),
    ])
    part_b = _transcript("en", [
        TranscriptSegment(text="world", start=0.0, end=4.0, speaker="SPEAKER_00"),
    ])

    merged = Transcript.concat([part_a, part_b], [0.0, 25.0])

    assert [s.text for s in merged.segments] == ["hello", "world"]
    assert merged.segments[0].start == 0.0 and merged.segments[0].end == 5.0
    assert merged.segments[0].words[0].start == 0.0 and merged.segments[0].words[0].end == 1.0
    assert merged.segments[1].start == 25.0 and merged.segments[1].end == 29.0
    assert merged.segments[1].speaker == "SPEAKER_00"
    assert merged.duration == 29.0
    assert merged.language == "en"


def test_concat_empty_list():
    merged = Transcript.concat([], [])
    assert merged.segments == []
    assert merged.duration == 0.0
    assert merged.language is None


def test_concat_trims_overlap_by_owned_range():
    # Part 0 owns [0, 25) but its audio (with overlap) extends past 25, part 1
    # owns [25, 50) but its audio starts at original-timeline offset 17 (25 -
    # 8s overlap). Both therefore transcribe the shared [17, 25) window. Only
    # the owning part's copy of it should survive the merge.
    part_a = _transcript("en", [
        TranscriptSegment(text="before", start=0.0, end=15.0),
        TranscriptSegment(text="overlap, part a's edge-degraded copy", start=26.0, end=30.0),
    ])
    part_b = _transcript("en", [
        TranscriptSegment(text="overlap, part b's edge-degraded copy", start=0.0, end=8.0),
        TranscriptSegment(text="after", start=8.0, end=33.0),
    ])

    merged = Transcript.concat(
        [part_a, part_b],
        offsets=[0.0, 17.0],
        owned_ranges=[(0.0, 25.0), (25.0, 50.0)],
    )

    assert [s.text for s in merged.segments] == ["before", "after"]
    assert merged.segments[1].start == 25.0


def test_concat_shifts_music_and_joins_a_range_cut_by_a_slice_boundary():
    # slices own [0, 100) and [100, 200), each carrying 8s of its neighbor
    part_a = Transcript(language="en", duration=108.0, segments=[], music=[(10.0, 20.0), (95.0, 108.0)])
    part_b = Transcript(language="en", duration=108.0, segments=[], music=[(0.0, 18.0), (50.0, 60.0)])

    merged = Transcript.concat([part_a, part_b], [0.0, 92.0], owned_ranges=[(0.0, 100.0), (100.0, 200.0)])

    # 95 to 110 is one piece of music seen by both slices
    assert merged.music == [(10.0, 20.0), (95.0, 110.0), (142.0, 152.0)]


def test_music_survives_a_json_round_trip_and_is_optional():
    t = Transcript(language="en", duration=5.0, segments=[], music=[(1.0, 2.5)])
    assert Transcript.from_json_dict(t.to_json_dict()).music == [(1.0, 2.5)]
    # transcripts written before music ranges existed
    assert Transcript.from_json_dict({"language": "en", "duration": 5.0, "segments": []}).music == []


def _worded(texts_and_starts):
    words = [Word(text=t, start=a, end=a + 0.4) for t, a in texts_and_starts]
    return TranscriptSegment(text=" ".join(w.text for w in words), start=words[0].start,
                             end=words[-1].end, words=words)


def test_concat_cuts_a_long_segment_at_the_owned_boundary_by_words():
    # one segment per slice, far longer than the 8s overlap. Slices own [0, 100) and [100, 200).
    part_a = _transcript("en", [_worded([("a", 10.0), ("b", 60.0), ("c", 99.0), ("d", 101.0), ("e", 107.0)])])
    # the same audio from 92s on, so local 7.0 is 99.0 and local 9.0 is 101.0
    part_b = _transcript("en", [_worded([("c", 7.0), ("d", 9.0), ("e", 15.0), ("f", 80.0)])])

    merged = Transcript.concat([part_a, part_b], [0.0, 92.0], owned_ranges=[(0.0, 100.0), (100.0, 200.0)])

    # nothing lost and nothing doubled, each word from the slice that owns it
    assert [s.text for s in merged.segments] == ["a b c", "d e f"]
    assert [w.start for s in merged.segments for w in s.words] == [10.0, 60.0, 99.0, 101.0, 107.0, 172.0]
    assert (merged.segments[0].end, merged.segments[1].start) == (99.4, 101.0)


def test_concat_keeps_whole_segments_that_do_not_cross_a_boundary():
    part_a = _transcript("en", [_worded([("a", 10.0), ("b", 11.0)])])
    merged = Transcript.concat([part_a], [0.0], owned_ranges=[(0.0, 100.0)])
    assert [s.text for s in merged.segments] == ["a b"]


def test_empty_music_is_left_out_of_the_json():
    # eval labels hash this JSON, so a transcript without music must serialize
    # the same as one saved before the field existed
    t = Transcript(language="en", duration=10.0, segments=[TranscriptSegment(text="hi", start=0.0, end=1.0)])
    assert "music" not in t.to_json_dict()
    assert Transcript.from_json_dict(t.to_json_dict()) == t
    with_music = Transcript(language="en", duration=10.0, segments=[], music=[(2.0, 5.0)])
    assert with_music.to_json_dict()["music"] == [(2.0, 5.0)]
    assert Transcript.from_json_dict(with_music.to_json_dict()) == with_music
