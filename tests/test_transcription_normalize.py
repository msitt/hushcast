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
            {"word": "world", "start": 11.0, "end": 11.5, "speaker": "spk:0"},
        ],
    })
    assert len(t.segments) == 1
    assert t.segments[0].text == "hello there world"
    assert (t.segments[0].start, t.segments[0].end) == (0.5, 11.5)
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
            {"word": "Back", "start": 5.0, "end": 5.4},
            {"word": "to", "start": 5.4, "end": 5.6},
            {"word": "the", "start": 5.6, "end": 5.8},
            {"word": "show.", "start": 5.8, "end": 6.4},
        ],
    })
    assert len(t.segments) == 1
    split = split_at_sentences(t)
    assert [s.text for s in split.segments] == ["Buy now.", "Back to the show."]
    assert split.segments[0].end == 1.0  # exact word end, not prorated
    assert split.segments[1].start == 5.0


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
