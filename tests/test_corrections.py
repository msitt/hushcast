import pytest

from hushcast.detection.corrections import (
    Correction,
    build_distill_messages,
    build_excerpt,
    parse_distill_response,
)
from hushcast.detection.prompts import Chunk, build_messages
from hushcast.transcription.base import Transcript, TranscriptSegment


def make_transcript():
    return Transcript(
        language="en", duration=100.0,
        segments=[TranscriptSegment(text=f"line {i}", start=i * 10.0, end=(i + 1) * 10.0) for i in range(10)],
    )


class TestExcerpt:
    def test_marks_inside_lines_and_includes_context(self):
        out = build_excerpt(make_transcript(), 40.0, 60.0)
        lines = out.splitlines()
        # 30s context on each side: lines from 10s..90s included
        assert any("line 1" in line for line in lines)
        assert not any("line 0" in line and line.startswith("»") for line in lines)
        marked = [line for line in lines if line.startswith("»")]
        assert len(marked) == 2  # lines 4 and 5 (40-50, 50-60)
        assert all("line 4" in line or "line 5" in line for line in marked)

    def test_far_lines_excluded(self):
        out = build_excerpt(make_transcript(), 0.0, 10.0)
        assert "line 9" not in out


class TestDistillMessages:
    def test_includes_corrections_and_current_hints(self):
        msgs = build_distill_messages(
            podcast_title="Show",
            user_hints="my hints",
            current_feed_hints="- old feed hint",
            current_global_hints="- old global hint",
            corrections=[
                Correction("false_positive", "Ep 1", "sponsor", "promo code", "» [0-10] text"),
                Correction("false_negative", "Ep 2", "ad", "", None),
            ],
        )
        user = msgs[1]["content"]
        assert "correction 1: false_positive" in user
        assert "correction 2: false_negative" in user
        assert "old feed hint" in user and "old global hint" in user
        assert "'sponsor'" in user and "promo code" in user


class TestParseDistill:
    def test_lists_become_bullets(self):
        feed, global_ = parse_distill_response(
            '{"feed_hints": ["a", "b"], "global_hints": ["c"]}'
        )
        assert feed == "- a\n- b"
        assert global_ == "- c"

    def test_empty_lists(self):
        feed, global_ = parse_distill_response('{"feed_hints": [], "global_hints": []}')
        assert feed == "" and global_ == ""

    def test_string_values_tolerated(self):
        feed, global_ = parse_distill_response('{"feed_hints": "just text", "global_hints": ""}')
        assert feed == "just text"

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_distill_response("no json here")


class TestPromptLayering:
    def test_order_and_conflict_rule(self):
        msgs = build_messages(
            system_prompt="sys",
            chunk=Chunk(text="[0.0-1.0] hi", start_s=0, end_s=1),
            podcast_title="Show",
            episode_title="Ep",
            detection_hints="user says X",
            learned_hints="- learned feed",
            global_learned_hints="- learned global",
        )
        user = msgs[1]["content"]
        assert user.index("learned global") < user.index("user says X") < user.index("learned feed")
        assert "podcast-specific guidance wins" in user

    def test_absent_layers_omitted(self):
        msgs = build_messages(
            system_prompt="sys",
            chunk=Chunk(text="[0.0-1.0] hi", start_s=0, end_s=1),
            podcast_title="Show",
            episode_title="Ep",
            detection_hints=None,
        )
        user = msgs[1]["content"]
        assert "learned" not in user and "guidance wins" not in user
