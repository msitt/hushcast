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
