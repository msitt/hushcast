import pytest

from hushcast.detection.llm import parse_json_object, parse_segments


def test_plain_json():
    out = parse_segments('{"segments": [{"start": 10, "end": 60, "category": "sponsor", "confidence": 0.9, "reason": "promo code"}]}')
    assert len(out) == 1
    assert out[0].category == "sponsor"


def test_markdown_fenced_json():
    content = 'Here you go:\n```json\n{"segments": [{"start": 1, "end": 2}]}\n```\nDone.'
    out = parse_segments(content)
    assert len(out) == 1
    assert out[0].category == "ad"  # default category


def test_leading_prose_and_trailing_junk():
    content = 'Sure! {"segments": []} hope that helps'
    assert parse_segments(content) == []


def test_invalid_category_coerced():
    out = parse_segments('{"segments": [{"start": 0, "end": 10, "category": "banana"}]}')
    assert out[0].category == "ad"


def test_bad_items_skipped():
    out = parse_segments('{"segments": [{"start": "x", "end": 10}, {"start": 5, "end": 10}, "junk"]}')
    assert len(out) == 1


def test_confidence_clamped():
    out = parse_segments('{"segments": [{"start": 0, "end": 10, "confidence": 7}]}')
    assert out[0].confidence == 1.0


def test_missing_segments_key_raises():
    with pytest.raises(ValueError):
        parse_segments('{"result": []}')


def test_no_json_raises():
    with pytest.raises(ValueError):
        parse_json_object("I could not find any ads.")
