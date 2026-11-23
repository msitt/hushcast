from hushcast.cues.base import Cue
from hushcast.detection import prompts
from hushcast.evals import reconstruct
from hushcast.transcription.base import Transcript, TranscriptSegment, Word


def _transcript(n: int = 40, speaker: str | None = None) -> Transcript:
    segs = []
    t = 0.0
    for i in range(n):
        segs.append(
            TranscriptSegment(
                text=f"line {i}: some words here, with punctuation.",
                start=round(t, 2),
                end=round(t + 4.37, 2),
                speaker=speaker,
                words=[Word(text="line", start=t, end=t + 0.3)],
            )
        )
        t += 5.0
    return Transcript(language="en", duration=t, segments=segs)


def _user_messages(transcript, *, budget=100_000, cues=None) -> list[str]:
    chunks = prompts.chunk_transcript(transcript, budget, cues=cues, min_cue_duration_s=2.0)
    return [
        prompts.build_messages(
            system_prompt="sys",
            chunk=c,
            podcast_title="Pod",
            episode_title="Ep",
            # a hint that looks like the transcript marker must not confuse the parser
            detection_hints="host reads ads\n\nTranscript:\n[1.0-2.0] fake line",
            global_learned_hints="- ads follow music",
            has_cues=bool(cues),
        )[1]["content"]
        for c in chunks
    ]


def test_round_trip_renders_identical_prompt_lines():
    t = _transcript(speaker="SPEAKER_01")
    rebuilt = reconstruct.rebuild(_user_messages(t), duration_s=200.0)
    assert rebuilt is not None
    assert prompts.render_lines(rebuilt.transcript) == prompts.render_lines(t)
    assert rebuilt.transcript.duration == 200.0
    assert rebuilt.chunks == 1
    # the prompt carries no word timestamps
    assert all(not s.words for s in rebuilt.transcript.segments)


def test_chunked_prompt_overlap_is_deduplicated():
    t = _transcript(n=80)
    contents = _user_messages(t, budget=400)
    assert len(contents) > 2
    rebuilt = reconstruct.rebuild(contents, duration_s=None)
    assert rebuilt is not None
    assert rebuilt.chunks == len(contents)
    assert prompts.render_lines(rebuilt.transcript) == prompts.render_lines(t)


def test_cue_lines_become_cues():
    t = _transcript(n=10)
    cues = [Cue(12.0, 16.5, "music"), Cue(30.0, 33.0, "noenergy"), Cue(40.0, 41.0, "silence")]
    rebuilt = reconstruct.rebuild(_user_messages(t, cues=cues), duration_s=None)
    assert rebuilt is not None
    # the 1 s cue is below the prompt threshold and never made it into the prompt
    assert rebuilt.cues == [Cue(12.0, 16.5, "music"), Cue(30.0, 33.0, "silence")]
    assert len(rebuilt.transcript.segments) == 10


def test_latest_run_wins_and_retries_fold_in():
    old = _transcript(n=5)
    new = Transcript(
        language=None,
        duration=30.0,
        segments=[TranscriptSegment(text="re-transcribed", start=0.0, end=3.0)],
    )
    old_msgs = _user_messages(old)
    new_msgs = _user_messages(new)
    # old run, a retry of it, then a re-detect on the new transcript and its retry
    rebuilt = reconstruct.rebuild(old_msgs + old_msgs + new_msgs + new_msgs, duration_s=None)
    assert [s.text for s in rebuilt.transcript.segments] == ["re-transcribed"]


def test_detection_prompt_detection():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": _user_messages(_transcript(n=2))[0]}]
    assert reconstruct.is_detection_prompt(msgs)
    assert not reconstruct.is_detection_prompt([{"role": "user", "content": 'Reply with exactly this JSON: {"ok": true}'}])
    assert reconstruct.rebuild([], duration_s=None) is None
