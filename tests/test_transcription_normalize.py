from hushcast.transcription.openai_compat import OpenAICompatTranscriber

normalize = OpenAICompatTranscriber._normalize


def test_flat_openai_shape():
    t = normalize({
        "language": "en", "duration": 20.0,
        "segments": [
            {"start": 0.0, "end": 10.0, "text": " hello ", "speaker": "SPEAKER_00", "words": [
                {"word": "hello", "start": 0.0, "end": 1.0},
            ]},
            {"start": 10.0, "end": 20.0, "text": "world", "speaker": "SPEAKER_01"},
        ],
    })
    assert len(t.segments) == 2
    assert t.segments[0].text == "hello"
    assert t.segments[0].words[0].text == "hello"
    assert t.segments[1].speaker == "SPEAKER_01"
    assert t.duration == 20.0


def test_single_speaker_stripped():
    # A lone distinct speaker (placeholder diarization) carries no information.
    t = normalize({
        "segments": [
            {"start": 0.0, "end": 10.0, "text": "hello", "speaker": "SPEAKER_00", "words": [
                {"word": "hello", "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
            ]},
            {"start": 10.0, "end": 20.0, "text": "world", "speaker": "SPEAKER_00"},
        ],
    })
    assert all(s.speaker is None for s in t.segments)
    assert t.segments[0].words[0].speaker is None


def test_whisperx_nested_aligned_shape():
    # whisperx-api-server: aligned output nests a dict under "segments"
    t = normalize({
        "language": "en",
        "segments": {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "one"},
                {"start": 5.0, "end": 9.5, "text": "two"},
            ],
            "word_segments": [{"word": "one", "start": 0.0, "end": 1.0}],
        },
    })
    assert [s.text for s in t.segments] == ["one", "two"]
    assert t.duration == 9.5  # falls back to last segment end


def test_garbage_entries_skipped():
    t = normalize({
        "duration": 10.0,
        "segments": ["not-a-dict", {"start": 0.0, "end": 10.0, "text": "ok", "words": ["junk"]}],
    })
    assert len(t.segments) == 1
    assert t.segments[0].words == []


def test_top_level_words_distributed_by_time():
    # OpenAI/Groq shape: words in a payload-level array, segments without them
    t = normalize({
        "duration": 20.0,
        "segments": [
            {"start": 0.0, "end": 10.0, "text": "hello there"},
            {"start": 10.0, "end": 20.0, "text": "world"},
        ],
        "words": [
            {"word": "hello", "start": 0.5, "end": 1.0},
            {"word": "there", "start": 1.2, "end": 1.8},
            {"word": "world", "start": 11.0, "end": 11.5},
        ],
    })
    assert [w.text for w in t.segments[0].words] == ["hello", "there"]
    assert [w.text for w in t.segments[1].words] == ["world"]


def test_top_level_word_in_gap_goes_to_nearer_segment():
    # timestamp drift: word midpoint falls between segments
    t = normalize({
        "segments": [
            {"start": 0.0, "end": 9.0, "text": "one"},
            {"start": 12.0, "end": 20.0, "text": "two"},
        ],
        "words": [
            {"word": "a", "start": 9.1, "end": 9.5},   # nearer segment 0's end
            {"word": "b", "start": 11.4, "end": 11.9},  # nearer segment 1's start
        ],
    })
    assert [w.text for w in t.segments[0].words] == ["a"]
    assert [w.text for w in t.segments[1].words] == ["b"]


def test_nested_words_win_over_top_level():
    # whisperx aligned output has both, the nested per-segment words are used
    t = normalize({
        "segments": [
            {"start": 0.0, "end": 10.0, "text": "hi", "words": [
                {"word": "hi", "start": 0.0, "end": 0.5},
            ]},
        ],
        "words": [{"word": "IGNORED", "start": 1.0, "end": 1.5}],
    })
    assert [w.text for w in t.segments[0].words] == ["hi"]


def test_whisperx_word_segments_distributed():
    # nested shape with per-segment words absent: word_segments are distributed
    t = normalize({
        "segments": {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "one"},
                {"start": 5.0, "end": 9.5, "text": "two"},
            ],
            "word_segments": [
                {"word": "one", "start": 0.2, "end": 0.9},
                {"word": "two", "start": 5.5, "end": 6.0},
            ],
        },
    })
    assert [w.text for w in t.segments[0].words] == ["one"]
    assert [w.text for w in t.segments[1].words] == ["two"]


def test_top_level_words_garbage_skipped():
    t = normalize({
        "segments": [{"start": 0.0, "end": 5.0, "text": "ok"}],
        "words": ["junk", {"word": "no-start"}, {"word": "fine", "start": 1.0, "end": 1.4}],
    })
    assert [w.text for w in t.segments[0].words] == ["fine"]


def test_empty_payload():
    t = normalize({})
    assert t.segments == []
    assert t.duration == 0.0


def test_words_only_payload_synthesizes_segments():
    # Gemini-style: word timings and text, no segments at all
    t = normalize({
        "text": "hello there world",
        "duration": 12.0,
        "words": [
            {"word": "hello", "start": 0.5, "end": 1.0, "speaker": "spk:0"},
            {"word": "there", "start": 1.2, "end": 1.8, "speaker": "spk:0"},
            {"word": "world", "start": 2.0, "end": 2.5, "speaker": "spk:0"},
        ],
    })
    assert len(t.segments) == 1
    assert t.segments[0].text == "hello there world"
    assert (t.segments[0].start, t.segments[0].end) == (0.5, 2.5)
    assert [w.text for w in t.segments[0].words] == ["hello", "there", "world"]
    assert t.duration == 12.0


def test_words_only_payload_splits_on_speaker_change():
    t = normalize({
        "words": [
            {"word": "hi", "start": 0.0, "end": 0.5, "speaker": "spk:0"},
            {"word": "there", "start": 0.5, "end": 1.0, "speaker": "spk:0"},
            {"word": "hello", "start": 2.0, "end": 2.4, "speaker": "spk:1"},
            {"word": "again", "start": 3.0, "end": 3.6, "speaker": "spk:0"},
        ],
    })
    assert [s.text for s in t.segments] == ["hi there", "hello", "again"]
    assert [s.speaker for s in t.segments] == ["spk:0", "spk:1", "spk:0"]
    assert (t.segments[0].start, t.segments[0].end) == (0.0, 1.0)
    assert t.duration == 3.6  # falls back to last segment end


def test_words_only_single_speaker_stripped():
    t = normalize({
        "words": [
            {"word": "one", "start": 0.0, "end": 0.5, "speaker": "spk:0"},
            {"word": "two", "start": 0.5, "end": 1.0, "speaker": "spk:0"},
        ],
    })
    assert t.segments[0].speaker is None
    assert all(w.speaker is None for w in t.segments[0].words)


def test_words_only_payload_aligns_for_sentence_splitting():
    # the synthesized text must tokenize 1:1 with its words so the sentence
    # splitter uses exact word timings instead of prorating by character
    from hushcast.transcription.sentences import split_at_sentences

    t = normalize({
        "words": [
            {"word": "Buy", "start": 0.0, "end": 0.4},
            {"word": "now.", "start": 0.4, "end": 1.0},
            {"word": "Back", "start": 1.5, "end": 1.9},
            {"word": "to", "start": 1.9, "end": 2.1},
            {"word": "the", "start": 2.1, "end": 2.3},
            {"word": "show.", "start": 2.3, "end": 2.9},
        ],
    })
    assert len(t.segments) == 1
    split = split_at_sentences(t)
    assert [s.text for s in split.segments] == ["Buy now.", "Back to the show."]
    assert split.segments[0].end == 1.0  # exact word end, not prorated
    assert split.segments[1].start == 1.5


def test_words_only_garbage_and_empty_text_skipped():
    t = normalize({
        "words": ["junk", {"word": "no-start"}, {"word": "", "start": 0.1, "end": 0.2},
                  {"word": "fine", "start": 1.0, "end": 1.4}],
    })
    assert [w.text for w in t.segments[0].words] == ["fine"]
    assert t.segments[0].text == "fine"


def test_segments_present_still_win_over_top_level_words():
    # regression: synthesis must not displace a provider's own segmentation
    t = normalize({
        "segments": [{"start": 0.0, "end": 10.0, "text": "hello there"}],
        "words": [{"word": "hello", "start": 0.5, "end": 1.0}],
    })
    assert [s.text for s in t.segments] == ["hello there"]
    assert [w.text for w in t.segments[0].words] == ["hello"]


def test_words_only_payload_breaks_at_long_pause():
    # no line may stretch across music or dead air
    t = normalize({
        "words": [
            {"word": "Thanks", "start": 0.0, "end": 0.4},
            {"word": "for", "start": 0.4, "end": 0.6},
            {"word": "joining.", "start": 0.6, "end": 1.0},
            {"word": "Who", "start": 33.0, "end": 33.2},
            {"word": "is", "start": 33.2, "end": 33.4},
            {"word": "this?", "start": 33.4, "end": 33.9},
        ],
    })
    assert [s.text for s in t.segments] == ["Thanks for joining.", "Who is this?"]
    assert (t.segments[0].end, t.segments[1].start) == (1.0, 33.0)


def _scribe_payload(**extra):
    # ElevenLabs Scribe via OpenRouter: one segment for the whole file, sound
    # tags listed among the words
    return {
        "language": "en", "duration": 120.0,
        "text": "[phone rings] Thanks for joining. [upbeat music] Who is this? It is me.",
        "segments": [{"id": 0, "start": 0.0, "end": 120.0,
                      "text": "[phone rings] Thanks for joining. [upbeat music] Who is this? It is me."}],
        "words": [
            {"word": "[phone rings]", "type": "audio_event", "start": 0.2, "end": 1.0},
            {"word": "Thanks", "start": 1.2, "end": 1.6},
            {"word": "for", "start": 1.6, "end": 1.8},
            {"word": "joining.", "start": 1.8, "end": 2.4},
            {"word": "[upbeat music]", "type": "audio_event", "start": 2.4, "end": 34.4},
            {"word": "Who", "start": 34.5, "end": 34.7},
            {"word": "is", "start": 34.7, "end": 34.9},
            {"word": "this?", "start": 34.9, "end": 35.4},
            {"word": "It", "start": 35.6, "end": 35.8},
            {"word": "is", "start": 35.8, "end": 36.0},
            {"word": "me.", "start": 36.0, "end": 36.5},
        ],
        **extra,
    }


def test_coarse_segment_replaced_by_lines_built_from_words():
    from hushcast.transcription.sentences import split_at_sentences

    t = normalize(_scribe_payload())
    assert [s.text for s in t.segments] == ["Thanks for joining.", "Who is this? It is me."]
    # every line tokenizes 1:1 with its words, so sentence splits use exact times
    assert all(len(s.words) == len(s.text.split()) for s in t.segments)
    split = split_at_sentences(t)
    assert [(s.text, s.start, s.end) for s in split.segments] == [
        ("Thanks for joining.", 1.2, 2.4),
        ("Who is this?", 34.5, 35.4),
        ("It is me.", 35.6, 36.5),
    ]
    assert t.duration == 120.0


def test_sound_tags_leave_the_text_and_music_is_kept_as_ranges():
    t = normalize(_scribe_payload())
    assert not any("[" in s.text for s in t.segments)
    assert t.music == [(2.4, 34.4)]  # the phone ring is not music


def test_coarse_segment_without_words_is_an_error():
    import pytest

    payload = _scribe_payload()
    del payload["words"]
    with pytest.raises(RuntimeError, match="word timestamps"):
        normalize(payload)


def test_long_segments_without_words_from_a_normal_provider_are_kept():
    # only a lone file-length segment is an error, an occasional long segment is not
    t = normalize({"segments": [
        {"start": 0.0, "end": 70.0, "text": "a long one"},
        {"start": 70.0, "end": 75.0, "text": "next"},
    ]})
    assert len(t.segments) == 2


def test_numeric_speaker_zero_survives():
    # providers that number speakers start at 0, which must not read as "no speaker"
    t = normalize({
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "hello", "speaker": 0},
            {"start": 5.0, "end": 9.0, "text": "hi", "speaker": 1},
        ],
    })
    assert [s.speaker for s in t.segments] == ["SPEAKER_00", "SPEAKER_01"]


def test_speakers_on_words_only_start_new_lines():
    payload = _scribe_payload()
    for w in payload["words"]:
        w["speaker"] = 1 if w["word"] in ("It", "is", "me.") and w["start"] > 35.5 else 0
    t = normalize(payload)
    assert [(s.speaker, s.text) for s in t.segments] == [
        ("SPEAKER_00", "Thanks for joining."),
        ("SPEAKER_00", "Who is this?"),
        ("SPEAKER_01", "It is me."),
    ]
