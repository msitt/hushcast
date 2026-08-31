from hushcast.transcription.base import Transcript, TranscriptSegment, Word
from hushcast.transcription.sentences import split_at_sentences


def _t(segments):
    return Transcript(language="en", duration=segments[-1].end, segments=segments)


def _words(text, start, end):
    """Evenly spaced word timestamps for a text, for test convenience."""
    tokens = text.split()
    per = (end - start) / len(tokens)
    return [
        Word(text=tok, start=start + i * per, end=start + (i + 1) * per)
        for i, tok in enumerate(tokens)
    ]


def test_single_sentence_untouched():
    t = _t([TranscriptSegment(text="Just one sentence here.", start=0.0, end=5.0)])
    assert split_at_sentences(t) is t


def test_split_with_word_timestamps():
    text = "for sponsor-supported jobs. U.S. tariffs have been shaping the economy for decades."
    seg = TranscriptSegment(text=text, start=706.0, end=717.0, words=_words(text, 706.0, 717.0))
    out = split_at_sentences(_t([seg]))
    assert [s.text for s in out.segments] == [
        "for sponsor-supported jobs.",
        "U.S. tariffs have been shaping the economy for decades.",
    ]
    first, second = out.segments
    # boundary comes from the sentence-final word's end / next word's start
    assert first.end == seg.words[2].end
    assert second.start == seg.words[3].start
    assert first.start == 706.0 and second.end == 717.0
    # words partitioned with the text
    assert [w.text for w in first.words] == ["for", "sponsor-supported", "jobs."]
    assert second.words[0].text == "U.S."


def test_no_split_inside_abbreviation():
    # "U.S. sanctions" must not split after "U.S." (lowercase next word AND
    # initialism); "No. Ten" style titles are also protected
    text = "U.S. sanctions continued. Mr. Smith agreed."
    seg = TranscriptSegment(text=text, start=0.0, end=8.0, words=_words(text, 0.0, 8.0))
    out = split_at_sentences(_t([seg]))
    assert [s.text for s in out.segments] == ["U.S. sanctions continued.", "Mr. Smith agreed."]


def test_no_split_when_next_word_lowercase():
    text = "It cost $4.99. per month he said"
    seg = TranscriptSegment(text=text, start=0.0, end=5.0)
    out = split_at_sentences(_t([seg]))
    assert len(out.segments) == 1


def test_interpolation_without_word_timestamps():
    # 2 sentences, no words: boundary prorated by character position
    text = "Short one. And then a much longer second sentence follows here."
    seg = TranscriptSegment(text=text, start=100.0, end=110.0)
    out = split_at_sentences(_t([seg]))
    assert len(out.segments) == 2
    a, b = out.segments
    assert a.start == 100.0 and b.end == 110.0
    assert a.end == b.start
    frac = len("Short one.") / len(text)
    assert abs(a.end - (100.0 + 10.0 * frac)) < 0.01


def test_mismatched_words_distributed_by_time():
    # word list doesn't align 1:1 with tokens -> interpolation, words re-attached by time
    text = "First sentence. Second sentence."
    words = [Word(text="first", start=0.0, end=1.0), Word(text="second", start=3.0, end=4.0)]
    seg = TranscriptSegment(text=text, start=0.0, end=4.0, words=words)
    out = split_at_sentences(_t([seg]))
    assert len(out.segments) == 2
    assert [w.text for w in out.segments[0].words] == ["first"]
    assert [w.text for w in out.segments[1].words] == ["second"]


def test_question_and_exclamation_split():
    text = "Hiring now? Then this is a job for you! Visit the site."
    seg = TranscriptSegment(text=text, start=0.0, end=6.0)
    out = split_at_sentences(_t([seg]))
    assert [s.text for s in out.segments] == [
        "Hiring now?",
        "Then this is a job for you!",
        "Visit the site.",
    ]


def test_speaker_preserved_and_quotes_handled():
    text = 'He said "Stop." Then we left.'
    seg = TranscriptSegment(text=text, start=0.0, end=4.0, speaker="SPEAKER_01")
    out = split_at_sentences(_t([seg]))
    assert [s.text for s in out.segments] == ['He said "Stop."', "Then we left."]
    assert all(s.speaker == "SPEAKER_01" for s in out.segments)


def test_multiple_segments_only_straddlers_split():
    segs = [
        TranscriptSegment(text="All one sentence here.", start=0.0, end=3.0),
        TranscriptSegment(text="Ad ends. Content starts now.", start=3.0, end=8.0),
    ]
    out = split_at_sentences(_t(segs))
    assert [s.text for s in out.segments] == [
        "All one sentence here.",
        "Ad ends.",
        "Content starts now.",
    ]
    assert out.segments[0] is segs[0]
