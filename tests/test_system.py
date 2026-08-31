"""Unit tests for the System page helpers: log ring buffer, provider labels."""
import logging

from hushcast.api.system import provider_host
from hushcast.logbuffer import RingBufferHandler


def make_logger(handler: RingBufferHandler) -> logging.Logger:
    logger = logging.getLogger("test_system_ring")
    logger.setLevel(logging.DEBUG)
    logger.handlers = [handler]
    logger.propagate = False
    return logger


def test_ring_buffer_captures_and_formats_args():
    handler = RingBufferHandler(capacity=10)
    log = make_logger(handler)
    log.info("processed %d episodes", 3)
    records = handler.tail()
    assert len(records) == 1
    assert records[0]["message"] == "processed 3 episodes"
    assert records[0]["level"] == "INFO"
    assert records[0]["logger"] == "test_system_ring"
    assert records[0]["ts"].endswith("+00:00")


def test_ring_buffer_evicts_oldest_at_capacity():
    handler = RingBufferHandler(capacity=3)
    log = make_logger(handler)
    for i in range(5):
        log.info("msg %d", i)
    assert [r["message"] for r in handler.tail()] == ["msg 2", "msg 3", "msg 4"]


def test_tail_filters_by_level_and_limit():
    handler = RingBufferHandler(capacity=10)
    log = make_logger(handler)
    log.debug("d")
    log.info("i1")
    log.warning("w")
    log.info("i2")
    assert [r["message"] for r in handler.tail(min_level=logging.INFO)] == ["i1", "w", "i2"]
    assert [r["message"] for r in handler.tail(limit=2, min_level=logging.INFO)] == ["w", "i2"]
    assert [r["message"] for r in handler.tail(min_level=logging.ERROR)] == []


def test_ring_buffer_includes_traceback():
    handler = RingBufferHandler(capacity=10)
    log = make_logger(handler)
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("step failed")
    msg = handler.tail()[0]["message"]
    assert "step failed" in msg
    assert "ValueError: boom" in msg


def test_provider_host():
    assert provider_host("https://api.openai.com/v1/chat/completions") == "api.openai.com"
    assert provider_host("http://localhost:11434/v1/chat/completions") == "localhost:11434"
    assert provider_host("not-a-url") == "not-a-url"
