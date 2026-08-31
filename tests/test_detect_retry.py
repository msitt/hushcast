import pytest

from hushcast.detection.llm import MALFORMED_RETRIES, ChatResult, _detect_chunk

GOOD = '{"segments": [{"start": 0.0, "end": 29.8, "category": "sponsor", "confidence": 0.9, "reason": "ok"}]}'
PROSE = "We need to identify ad segments. Let's go through the transcript lines."
TRUNCATED = '{"segments": [{"start": 0.0, "end": 29.8, "category": "sponsor", "confidence": 0.9, "reason": "ok"}, {"start": 100.0, "en'

MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "transcript"}]


class StubLLM:
    def __init__(self, replies: list[ChatResult]):
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages, *, schema=None, recorder=None):
        self.calls.append(messages)
        return self.replies.pop(0)


async def run(replies, log_lines=None):
    llm = StubLLM(replies)
    segments = await _detect_chunk(
        llm, MESSAGES, chunk_label="chunk 1/1", log_lines=log_lines, recorder=None
    )
    return llm, segments


async def test_good_first_reply_no_retry():
    llm, segments = await run([ChatResult(GOOD, "stop")])
    assert len(llm.calls) == 1
    assert len(segments) == 1


async def test_prose_reply_retried_with_nudge():
    log: list[str] = []
    llm, segments = await run([ChatResult(PROSE, "stop"), ChatResult(GOOD, "stop")], log)
    assert len(llm.calls) == 2
    # the retry keeps the original messages and appends one corrective user turn
    assert llm.calls[1][: len(MESSAGES)] == MESSAGES
    assert llm.calls[1][-1]["role"] == "user"
    assert "ONLY the JSON object" in llm.calls[1][-1]["content"]
    assert any("malformed" in line for line in log)


async def test_truncated_reply_retried_then_good_wins():
    llm, segments = await run([ChatResult(TRUNCATED, "length"), ChatResult(GOOD, "stop")])
    assert len(llm.calls) == 2
    assert len(segments) == 1


async def test_all_truncated_uses_salvage():
    log: list[str] = []
    replies = [ChatResult(TRUNCATED, "length")] * (1 + MALFORMED_RETRIES)
    llm, segments = await run(replies, log)
    assert len(llm.calls) == 1 + MALFORMED_RETRIES
    # the complete first segment survives the truncation repair
    assert len(segments) == 1
    assert segments[0].end == 29.8
    assert any("salvaged" in line for line in log)


async def test_all_malformed_raises():
    replies = [ChatResult(PROSE, "stop")] * (1 + MALFORMED_RETRIES)
    with pytest.raises(RuntimeError, match="malformed output"):
        await run(replies)


async def test_parsed_but_truncated_without_length_flag_is_accepted():
    # servers that don't report finish_reason: salvageable JSON is used as-is
    llm, segments = await run([ChatResult(TRUNCATED, None)])
    assert len(llm.calls) == 1
    assert len(segments) == 1
