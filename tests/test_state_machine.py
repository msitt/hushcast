import pytest

from hushcast.pipeline import state


def test_happy_path():
    path = [
        state.DISCOVERED, state.QUEUED, state.DOWNLOADING, state.TRANSCRIBING,
        state.DETECTING, state.CUTTING, state.PROCESSED,
    ]
    for current, new in zip(path, path[1:], strict=False):
        state.validate_transition(current, new)


def test_whitelisted_copy_through():
    state.validate_transition(state.DOWNLOADING, state.CUTTING)


def test_skipped_can_be_queued_manually():
    state.validate_transition(state.SKIPPED, state.QUEUED)


def test_failed_retry():
    state.validate_transition(state.TRANSCRIBING, state.FAILED)
    state.validate_transition(state.FAILED, state.QUEUED)


def test_failed_can_be_dismissed_to_skipped():
    state.validate_transition(state.FAILED, state.SKIPPED)
    # but not the other way around, and never straight to processed
    with pytest.raises(state.InvalidTransition):
        state.validate_transition(state.SKIPPED, state.FAILED)
    with pytest.raises(state.InvalidTransition):
        state.validate_transition(state.FAILED, state.PROCESSED)


def test_processed_reprocess_and_expiry():
    state.validate_transition(state.PROCESSED, state.QUEUED)
    state.validate_transition(state.PROCESSED, state.EXPIRED)


def test_invalid_transitions_raise():
    with pytest.raises(state.InvalidTransition):
        state.validate_transition(state.DISCOVERED, state.PROCESSED)
    with pytest.raises(state.InvalidTransition):
        state.validate_transition(state.SKIPPED, state.DOWNLOADING)
