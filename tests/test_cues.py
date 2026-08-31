import json

from hushcast.cues.base import Cue, cues_from_json, cues_to_json
from hushcast.cues.ina_client import parse_ina_response
from hushcast.cues.silencedetect import parse_silencedetect

FFMPEG_STDERR = """\
Input #0, mp3, from 'ep.mp3':
  Duration: 00:30:00.00, start: 0.000000, bitrate: 128 kb/s
[silencedetect @ 0x55d] silence_start: 61.523
[silencedetect @ 0x55d] silence_end: 63.001 | silence_duration: 1.478
[silencedetect @ 0x55d] silence_start: 120.5
[silencedetect @ 0x55d] silence_end: 122.25 | silence_duration: 1.75
size=N/A time=00:30:00.00 bitrate=N/A speed= 500x
"""


class TestParseSilencedetect:
    def test_extracts_pairs(self):
        assert parse_silencedetect(FFMPEG_STDERR) == [(61.523, 63.001), (120.5, 122.25)]

    def test_unmatched_trailing_start_closed_at_duration(self):
        stderr = FFMPEG_STDERR + "[silencedetect @ 0x55d] silence_start: 1790.0\n"
        out = parse_silencedetect(stderr, total_duration=1800.0)
        assert out[-1] == (1790.0, 1800.0)

    def test_unmatched_trailing_start_dropped_without_duration(self):
        stderr = FFMPEG_STDERR + "[silencedetect @ 0x55d] silence_start: 1790.0\n"
        assert len(parse_silencedetect(stderr)) == 2

    def test_negative_start_clamped(self):
        stderr = "silence_start: -0.01\nsilence_end: 2.0\n"
        assert parse_silencedetect(stderr) == [(0.0, 2.0)]

    def test_empty(self):
        assert parse_silencedetect("no silences here") == []


class TestParseInaResponse:
    def test_dict_items_with_stop(self):
        payload = [
            {"label": "music", "start": 0.0, "stop": 12.5},
            {"label": "speech", "start": 12.5, "stop": 300.0},
            {"label": "noEnergy", "start": 300.0, "stop": 302.0},
        ]
        cues = parse_ina_response(payload)
        assert [c.kind for c in cues] == ["music", "noenergy"]

    def test_triple_items(self):
        cues = parse_ina_response([["music", 0.0, 10.0], ["male", 10.0, 60.0]])
        assert cues == [Cue(start=0.0, end=10.0, kind="music")]

    def test_nested_segments_key(self):
        cues = parse_ina_response({"segments": [{"label": "noise", "start": 1, "end": 3}]})
        assert cues == [Cue(start=1.0, end=3.0, kind="noise")]

    def test_garbage_items_skipped(self):
        cues = parse_ina_response([{"label": "music", "start": "x", "stop": 3}, "junk", ["music", 5]])
        assert cues == []


class TestJsonRoundTrip:
    def test_round_trip(self):
        cues = [Cue(start=0.0, end=5.0, kind="music"), Cue(start=10.0, end=12.0, kind="silence")]
        assert cues_from_json(json.loads(json.dumps(cues_to_json(cues)))) == cues

    def test_unknown_kind_dropped(self):
        assert cues_from_json([{"start": 0, "end": 1, "kind": "speech"}]) == []
