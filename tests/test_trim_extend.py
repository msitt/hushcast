from hushcast.api.episodes import extend_trim_over_silence

# episode 2519's pre-roll: block 0-30.12, the last two lines trimmed
LINES = [(24.625, 26.865), (27.08, 28.68), (28.68, 29.88), (30.36, 33.8)]


def test_extends_over_trailing_silence():
    assert extend_trim_over_silence(27.08, 29.88, [(0.0, 30.12)], LINES) == (27.08, 30.12)


def test_extends_over_leading_silence():
    lines = [(0.5, 3.0), (3.2, 6.0), (6.5, 20.0)]
    assert extend_trim_over_silence(0.5, 6.0, [(0.0, 20.0)], lines) == (0.0, 6.0)


def test_speech_left_in_the_block_stays_an_ad():
    # trimming the middle line keeps the ad lines on both sides
    assert extend_trim_over_silence(27.08, 28.68, [(0.0, 30.12)], LINES) == (27.08, 28.68)


def test_line_just_touching_the_piece_is_not_speech():
    # rebuilt transcripts round to 0.1 s: a line ending 0.05 s into the piece
    lines = [(10.0, 20.05), (20.1, 25.0)]
    assert extend_trim_over_silence(20.1, 25.0, [(0.0, 26.0)], lines) == (20.1, 26.0)
    assert extend_trim_over_silence(20.1, 25.0, [(20.0, 26.0)], lines) == (20.0, 26.0)
    # but 0.15 s of the line in the piece is speech
    assert extend_trim_over_silence(20.1, 25.0, [(19.9, 26.0)], lines) == (20.1, 26.0)


def test_overlapping_blocks_are_one_block():
    assert extend_trim_over_silence(27.08, 29.88, [(0.0, 29.0), (28.0, 30.12)], LINES) == (27.08, 30.12)


def test_trim_past_the_block_edge_is_unchanged():
    assert extend_trim_over_silence(27.08, 31.0, [(0.0, 30.12)], LINES) == (27.08, 31.0)


def test_no_lines_extends_to_both_edges():
    # every leftover piece is speech-free when the transcript has no lines
    assert extend_trim_over_silence(5.0, 10.0, [(0.0, 12.0)], []) == (0.0, 12.0)
