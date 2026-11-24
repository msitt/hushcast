import json

import pytest

from hushcast import settings_store
from hushcast.detection import llm as llm_mod
from hushcast.detection.finalize import MAX_DETECT_ATTEMPTS, detect_and_finalize
from hushcast.detection.llm import DetectionResult
from hushcast.detection.segments import (
    AdSegment,
    DetectionRejected,
    check_segment_length,
)
from hushcast.evals import runner
from hushcast.transcription.base import Transcript

DURATION = 3000.0
# one line every 10 s, so every multiple of 10 is a snap boundary
TRANSCRIPT = Transcript.from_json_dict({
    "language": None,
    "duration": DURATION,
    "segments": [{"text": f"line {i}", "start": i * 10.0, "end": i * 10.0 + 9.5} for i in range(300)],
})
SETTINGS = dict(settings_store.DEFAULTS)

GOOD = DetectionResult([AdSegment(0.0, 90.0, "ad"), AdSegment(940.0, 1130.0, "ad")])
# the start of the first break joined to the end of the second, 18 min of show cut
JOINED = DetectionResult([AdSegment(0.0, 1130.0, "ad")])


class Detector:
    def __init__(self, *results: DetectionResult):
        self.results = list(results)
        self.attempts: list[int] = []

    async def __call__(self, attempt: int) -> DetectionResult:
        self.attempts.append(attempt)
        return self.results.pop(0)


async def run(detector, settings=SETTINGS, log=None):
    return await detect_and_finalize(
        detector, transcript=TRANSCRIPT, cues=None, duration=DURATION, settings=settings, log_lines=log
    )


def test_segment_length_check():
    check_segment_length([AdSegment(0.0, 360.0, "ad")], 360.0)  # at the limit is fine
    check_segment_length([AdSegment(0.0, 2000.0, "ad")], 0.0)  # 0 = off
    with pytest.raises(DetectionRejected) as exc:
        check_segment_length([AdSegment(10.0, 60.0, "ad"), AdSegment(48.0, 968.5, "ad")], 360.0)
    assert exc.value.retryable
    assert "0:48-16:08" in str(exc.value)


async def test_good_first_detection_runs_once():
    detector = Detector(GOOD)
    _, done = await run(detector)
    assert detector.attempts == [0]
    assert done.outcome == "cut" and done.attempts == 1


async def test_overlong_segment_is_detected_again():
    detector, log = Detector(JOINED, GOOD), []
    result, done = await run(detector, log=log)
    assert detector.attempts == [0, 1]
    assert result is GOOD
    assert done.outcome == "cut" and done.attempts == 2
    assert [(s.start, s.end) for s in done.segments] == [(0.0, 90.0), (940.0, 1130.0)]
    assert "detecting again" in log[0]


async def test_overlong_twice_goes_to_review():
    detector = Detector(*[JOINED] * MAX_DETECT_ATTEMPTS)
    _, done = await run(detector)
    assert len(detector.attempts) == MAX_DETECT_ATTEMPTS
    assert done.outcome == "review" and done.attempts == MAX_DETECT_ATTEMPTS
    assert f"after {MAX_DETECT_ATTEMPTS} detections" in done.rejection


async def test_too_much_removed_is_not_retried():
    # many normal-length segments over half the episode: not a one-off slip
    many = DetectionResult([AdSegment(i * 400.0, i * 400.0 + 300.0, "ad") for i in range(7)])
    detector = Detector(many)
    _, done = await run(detector)
    assert detector.attempts == [0]
    assert done.outcome == "review" and not done.retryable
    assert "after" not in done.rejection


async def test_promo_episode_skips_length_check():
    promo = DetectionResult([AdSegment(0.0, 2900.0, "self_promo")], whole_episode_promo=True)
    detector = Detector(promo)
    _, done = await run(detector)
    assert detector.attempts == [0]
    assert done.outcome == "promo"


async def test_length_check_off():
    detector = Detector(JOINED)
    _, done = await run(detector, settings={**SETTINGS, "max_segment_s": 0})
    assert done.outcome == "cut" and done.attempts == 1


async def test_eval_runner_redetects_with_its_own_cache_entry(tmp_path, monkeypatch):
    calls = []

    async def fake_chat(self, messages, *, schema=None, recorder=None):
        calls.append(messages)
        segments = (
            [{"start": 0.0, "end": 1130.0, "category": "ad", "reason": "x"}]
            if len(calls) == 1
            else [{"start": 940.0, "end": 1130.0, "category": "ad", "reason": "x"}]
        )
        return llm_mod.ChatResult(
            content=json.dumps({"segments": segments, "whole_episode_promo": False}), finish_reason="stop"
        )

    monkeypatch.setattr(llm_mod.LLMClient, "chat", fake_chat)
    settings = {**SETTINGS, "llm_base_url": "http://llm.invalid/v1", "llm_model": "fake",
                "llm_context_budget_tokens": 100000}
    episode = {
        "episode_id": 1,
        "duration_s": DURATION,
        "transcript": TRANSCRIPT.to_json_dict(),
        "cues": None,
    }

    def make_client(stats):
        return runner.CachingClient(settings, stats=stats, cache_dir=tmp_path, repeat=0)

    first = await runner.run_episode(episode, 0, settings=settings, hints={}, make_client=make_client)
    assert len(calls) == 2  # same messages both times, but the retry was not served the cached first answer
    assert calls[0] == calls[1]
    assert first["attempts"] == 2 and first["calls"] == 2
    assert first["outcome"] == "cut" and first["segments"] == [[940.0, 1130.0, "ad"]]

    # a rerun replays both attempts from the cache
    again = await runner.run_episode(episode, 0, settings=settings, hints={}, make_client=make_client)
    assert len(calls) == 2
    assert again["attempts"] == 2 and again["cached_calls"] == 2
    assert again["segments"] == first["segments"]
