"""Human-readable step error descriptions (worker._describe_error)."""
import httpx

from hushcast.pipeline.worker import _describe_error


def test_read_timeout_names_setting_and_value():
    msg = _describe_error(httpx.ReadTimeout(""), "transcribe", {"transcription_timeout_s": 600})
    assert msg == (
        "timed out: no response from the server within 600s, "
        "consider raising the timeout under Settings → Transcription → Timeout"
    )


def test_timeout_on_step_without_configurable_timeout_is_generic():
    msg = _describe_error(httpx.ReadTimeout(""), "download", {})
    assert msg == "timed out: the server did not respond in time"


def test_connect_error_is_friendly_even_when_blank():
    msg = _describe_error(httpx.ConnectError(""), "transcribe", {})
    assert msg == "could not connect to the server (connection refused or unreachable)"


def test_other_errors_keep_type_and_detail():
    msg = _describe_error(ValueError("bad json"), "detect", {})
    assert msg == "ValueError: bad json"


def test_blank_error_falls_back_to_type_name():
    assert _describe_error(RuntimeError(""), "detect", {}) == "RuntimeError"
