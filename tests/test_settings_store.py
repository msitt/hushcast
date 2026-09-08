"""Merging stored settings over DEFAULTS."""
from hushcast.settings_store import DEFAULT_DETECTION_PROMPT, DEFAULTS, merge_settings


def test_empty_stored_returns_defaults():
    assert merge_settings({}) == DEFAULTS


def test_stored_value_overrides_default():
    merged = merge_settings({"detection_prompt": "my own prompt", "mp3_quality": 2})
    assert merged["detection_prompt"] == "my own prompt"
    assert merged["mp3_quality"] == 2


def test_blank_detection_prompt_resets_to_default():
    for blank in ("", "   ", "\n\t "):
        assert merge_settings({"detection_prompt": blank})["detection_prompt"] == DEFAULT_DETECTION_PROMPT


def test_blank_is_preserved_for_other_keys():
    # only detection_prompt opts into reset-on-empty, empty is meaningful elsewhere
    assert merge_settings({"feed_token": ""})["feed_token"] == ""
    assert merge_settings({"global_learned_hints": ""})["global_learned_hints"] == ""


def test_unknown_stored_keys_are_dropped():
    merged = merge_settings({"nope": 1, "mp3_quality": 7})
    assert "nope" not in merged
    assert merged["mp3_quality"] == 7
