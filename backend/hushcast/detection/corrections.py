"""Correction capture helpers and the hint distiller.

Corrections are training signal only: a user marking a detected segment as
"not an ad" (false positive) or adding a missed ad range (false negative)
never re-cuts audio by itself. The distiller turns a feed's accumulated
corrections into concise detection guidance (per-feed and global) which the
user reviews before applying.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..transcription.base import Transcript
from .llm import LLMClient, parse_json_object
from .prompts import render_lines

log = logging.getLogger(__name__)

EXCERPT_CONTEXT_S = 30.0
MAX_CORRECTIONS_IN_PROMPT = 60  # newest first, enough signal without unbounded prompts

DISTILL_SCHEMA: dict[str, Any] = {
    "name": "distilled_hints",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "feed_hints": {"type": "array", "items": {"type": "string"}},
            "global_hints": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["feed_hints", "global_hints"],
        "additionalProperties": False,
    },
}


def build_excerpt(transcript: Transcript, start_s: float, end_s: float) -> str:
    """Transcript lines overlapping [start-ctx, end+ctx], marked in/out of range."""
    lo = start_s - EXCERPT_CONTEXT_S
    hi = end_s + EXCERPT_CONTEXT_S
    lines = []
    for seg, line in zip(transcript.segments, render_lines(transcript), strict=False):
        if seg.end < lo or seg.start > hi:
            continue
        inside = seg.start < end_s and seg.end > start_s
        lines.append(("» " if inside else "  ") + line)
    return "\n".join(lines)


@dataclass
class Correction:
    kind: str  # "false_positive" | "false_negative"
    episode_title: str
    category: str
    reason: str
    excerpt: str | None


DISTILL_PROMPT = """\
You maintain detection guidance for an automated podcast ad-removal system.
An LLM flags ad/sponsor/self-promo segments in transcripts, a human has
reviewed some of its output and made corrections. Your job is to distill ALL
the corrections below into concise guidance that prevents these mistakes on
FUTURE episodes.

Correction kinds:
- false_positive: the detector flagged this span, but the human says it is
  NOT an ad, guidance should stop this kind of over-flagging.
- false_negative: the detector missed this span, but the human says it IS an
  ad, guidance should help catch this kind of ad.

Rules:
- Output generalizable PATTERNS, never episode-specific instances. Bad:
  "don't flag the segment at 12:30 of episode 178". Good: "the host's
  book-recommendation segment is editorial content, not sponsorship".
- Classify each pattern's scope:
  - "feed_hints": specific to THIS podcast (its hosts, recurring segments,
    its sponsors' style).
  - "global_hints": show-independent ad mechanics that apply to any podcast
    (promo-code phrasing, lead-in/outro phrasing, production or voice
    discontinuities, network insertion markers). Only use global when the
    pattern is clearly show-independent.
- You are REGENERATING the guidance from scratch: the current guidance is
  provided for reference, consolidate it with the corrections, drop anything
  the corrections contradict, and merge duplicates.
- Be concise: at most ~10 short bullet points for feed_hints and ~5 for
  global_hints. Every word rides along on all future detection calls.
- If the corrections support no guidance for a scope, return an empty list
  for it.

Respond with ONLY a JSON object in exactly this shape:
{"feed_hints": ["...", "..."], "global_hints": ["..."]}
"""


def build_distill_messages(
    *,
    podcast_title: str,
    user_hints: str | None,
    current_feed_hints: str | None,
    current_global_hints: str | None,
    corrections: list[Correction],
) -> list[dict[str, str]]:
    parts = [f'Podcast: "{podcast_title}"']
    if user_hints:
        parts.append(f"User-written guidance for this podcast (do NOT restate it, it is always included):\n{user_hints}")
    if current_feed_hints:
        parts.append(f"Current learned guidance for this podcast (regenerate/consolidate):\n{current_feed_hints}")
    if current_global_hints:
        parts.append(f"Current global learned guidance (regenerate/consolidate):\n{current_global_hints}")
    blocks = []
    for i, c in enumerate(corrections[:MAX_CORRECTIONS_IN_PROMPT], 1):
        block = [f"--- correction {i}: {c.kind} ---", f'episode: "{c.episode_title}"']
        if c.kind == "false_positive":
            block.append(f"the detector called this {c.category!r}" + (f' because: "{c.reason}"' if c.reason else ""))
        if c.excerpt:
            block.append("transcript excerpt (» marks the corrected span):\n" + c.excerpt)
        blocks.append("\n".join(block))
    parts.append("Corrections:\n\n" + "\n\n".join(blocks))
    return [
        {"role": "system", "content": DISTILL_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_distill_response(content: str) -> tuple[str, str]:
    """Returns (feed_hints, global_hints) as newline-bulleted text."""
    obj = parse_json_object(content)

    def to_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(f"- {str(item).strip()}" for item in value if str(item).strip())
        return ""

    return to_text(obj.get("feed_hints")), to_text(obj.get("global_hints"))


async def distill(
    llm: LLMClient,
    *,
    podcast_title: str,
    user_hints: str | None,
    current_feed_hints: str | None,
    current_global_hints: str | None,
    corrections: list[Correction],
) -> tuple[str, str]:
    messages = build_distill_messages(
        podcast_title=podcast_title,
        user_hints=user_hints,
        current_feed_hints=current_feed_hints,
        current_global_hints=current_global_hints,
        corrections=corrections,
    )
    result = await llm.chat(messages, schema=DISTILL_SCHEMA)
    return parse_distill_response(result.content)
