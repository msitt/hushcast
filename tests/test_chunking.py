from hushcast.cues.base import Cue
from hushcast.detection.prompts import (
    CUE_EXPLANATION,
    boundaries,
    build_lines,
    build_messages,
    chunk_transcript,
    render_lines,
)
from hushcast.transcription.base import Transcript, TranscriptSegment


def make_transcript(n_segments=100, seg_len=10.0, text="hello world this is a test sentence"):
    segments = [
        TranscriptSegment(text=text, start=i * seg_len, end=(i + 1) * seg_len)
        for i in range(n_segments)
    ]
    return Transcript(language="en", duration=n_segments * seg_len, segments=segments)


def test_single_chunk_when_fits():
    t = make_transcript(50)
    chunks = chunk_transcript(t, budget_tokens=90000)
    assert len(chunks) == 1
    assert chunks[0].start_s == 0.0
    assert chunks[0].end_s == t.duration


def test_splits_with_overlap_when_over_budget():
    t = make_transcript(400)
    # tiny budget forces multiple chunks
    chunks = chunk_transcript(t, budget_tokens=500, overlap_s=60.0)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.start_s < prev.end_s  # overlapping
        assert prev.end_s - nxt.start_s >= 30  # meaningful overlap
    assert chunks[0].start_s == 0.0
    assert chunks[-1].end_s == t.duration


def test_render_lines_includes_speaker():
    t = Transcript(
        language="en", duration=10,
        segments=[TranscriptSegment(text="hi", start=0, end=5, speaker="SPEAKER_00")],
    )
    assert render_lines(t) == ["[0.0-5.0] SPEAKER_00: hi"]


def test_boundaries_sorted_unique():
    t = make_transcript(3)
    assert boundaries(t) == [0.0, 10.0, 20.0, 30.0]


def test_cue_lines_interleaved_in_time_order():
    t = make_transcript(3)
    cues = [Cue(start=12.0, end=17.2, kind="music"), Cue(start=25.0, end=28.0, kind="noenergy")]
    lines = [line.text for line in build_lines(t, cues)]
    assert lines[2] == "[12.0-17.2] [MUSIC] (5.2s)"  # after the 10-20 line's start
    assert "[25.0-28.0] [SILENCE] (3.0s)" in lines  # noenergy renders as SILENCE


def test_short_cues_hidden_from_prompt():
    t = make_transcript(3)
    cues = [Cue(start=12.0, end=12.5, kind="silence")]
    lines = [line.text for line in build_lines(t, cues, min_cue_duration_s=2.0)]
    assert len(lines) == 3


def test_boundaries_unaffected_by_cues():
    t = make_transcript(3)
    chunks = chunk_transcript(t, budget_tokens=90000, cues=[Cue(start=5.0, end=8.0, kind="music")])
    assert "[5.0-8.0] [MUSIC]" in chunks[0].text
    assert boundaries(t) == [0.0, 10.0, 20.0, 30.0]  # cue timestamps never become legal cut points


def test_chunking_with_cues_still_overlaps():
    t = make_transcript(400)
    cues = [Cue(start=i * 100.0, end=i * 100.0 + 5.0, kind="music") for i in range(40)]
    chunks = chunk_transcript(t, budget_tokens=500, overlap_s=60.0, cues=cues)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.start_s < prev.end_s


def test_cue_explanation_only_when_cues_present():
    t = make_transcript(3)
    chunk = chunk_transcript(t, budget_tokens=90000)[0]
    common = dict(system_prompt="sys", chunk=chunk, podcast_title="p", episode_title="e", detection_hints=None)
    without = build_messages(**common)
    with_cues = build_messages(**common, has_cues=True)
    assert CUE_EXPLANATION not in without[1]["content"]
    assert CUE_EXPLANATION in with_cues[1]["content"]
