"""How many parts an upload is sent in."""
from hushcast.pipeline.steps.transcribe import part_count

MB = 1_000_000


def test_under_both_limits_goes_out_whole():
    assert part_count(10 * MB, 24 * MB, 1800, 0) == 1
    assert part_count(10 * MB, 24 * MB, 1800, 1800) == 1


def test_size_cap_alone():
    # 5% safety margin: 50 MB over a 24 MB cap needs 3 parts
    assert part_count(50 * MB, 24 * MB, 7200, 0) == 3
    # barely over the cap is still at least 2
    assert part_count(24.5 * MB, 24 * MB, 7200, 0) == 2


def test_part_length_alone():
    # a 74 minute episode with a 30 minute limit
    assert part_count(13 * MB, 24 * MB, 74 * 60, 30 * 60) == 3
    assert part_count(13 * MB, 0, 74 * 60, 30 * 60) == 3


def test_stricter_limit_wins():
    assert part_count(50 * MB, 24 * MB, 4 * 3600, 30 * 60) == 8
    assert part_count(100 * MB, 24 * MB, 40 * 60, 30 * 60) == 5
