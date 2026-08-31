"""OpenAI-compatible chat-completions client for ad detection."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ..cues.base import Cue
from ..errors import RateLimitError, parse_retry_after
from ..transcription.base import Transcript
from . import prompts
from .segments import AdSegment

# Called after each successful chat request so callers can persist the exchange.
CallRecorder = Callable[[dict[str, Any]], Awaitable[None]]

log = logging.getLogger(__name__)

VALID_CATEGORIES = {"ad", "sponsor", "self_promo"}

# Extra attempts per chunk when the LLM reply is malformed (no parseable JSON)
# or cut off by the completion token limit before the JSON was finished.
MALFORMED_RETRIES = 2

RETRY_NUDGE = (
    "Your previous reply did not contain a complete JSON object ({error}). "
    "Respond with ONLY the JSON object in the requested shape. Do not include "
    "analysis, reasoning, or any other text."
)

# response_format json_schema for detection. Providers that enforce it (OpenAI,
# OpenRouter structured outputs, vLLM guided decoding) constrain sampling to
# this shape. Providers that don't may silently ignore it, so parsing stays tolerant.
SEGMENTS_SCHEMA: dict[str, Any] = {
    "name": "ad_segments",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["start", "end", "category", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["segments"],
        "additionalProperties": False,
    },
}


@dataclass
class ChatResult:
    content: str
    finish_reason: str | None


class LLMClient:
    def __init__(self, settings: dict[str, Any]):
        self.base_url = settings["llm_base_url"].rstrip("/")
        self.api_key = settings["llm_api_key"]
        self.model = settings["llm_model"]
        self.temperature = float(settings["llm_temperature"])
        self.max_tokens = int(settings.get("llm_max_tokens") or 16384)
        self.timeout_s = float(settings["llm_timeout_s"])

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        recorder: CallRecorder | None = None,
    ) -> ChatResult:
        if not self.base_url or not self.model:
            raise RuntimeError("llm_base_url / llm_model are not configured")
        if schema is not None:
            response_format: dict[str, Any] = {"type": "json_schema", "json_schema": schema}
        else:
            response_format = {"type": "json_object"}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": response_format,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        input_chars = sum(len(m["content"]) for m in messages)
        for m in messages:
            log.debug("LLM request [%s]:\n%s", m["role"], m["content"])
        events: list[str] = []
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=30.0)) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            # graded fallback for servers that reject the structured form:
            # json_schema -> json_object -> no response_format at all
            if (
                resp.status_code == 400
                and "response_format" in resp.text
                and body["response_format"]["type"] == "json_schema"
            ):
                    events.append("server rejected json_schema response_format (HTTP 400), retried with json_object")
                    log.info("LLM rejected json_schema response_format, retrying with json_object")
                    body["response_format"] = {"type": "json_object"}
                    resp = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            if resp.status_code == 400 and "response_format" in resp.text:
                events.append("server rejected response_format (HTTP 400), retried without it")
                log.info("LLM rejected response_format, retrying without it")
                body.pop("response_format")
                resp = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        elapsed = time.monotonic() - started

        # the request body actually sent (after fallbacks), minus the bulky messages
        params = {k: v for k, v in body.items() if k != "messages"}

        async def record(*, response_text: str, response_json: Any, finish_reason: str | None,
                         usage: dict[str, Any]) -> None:
            if recorder is None:
                return
            await recorder({
                "url": f"{self.base_url}/chat/completions",
                "model": self.model,
                "messages": messages,
                "params": params,
                "response": response_text,
                "response_json": response_json,
                "events": events,
                "status_code": resp.status_code,
                "finish_reason": finish_reason,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "elapsed_s": round(elapsed, 2),
            })

        if resp.status_code != 200:
            events.append(f"request failed with HTTP {resp.status_code}")
            await record(response_text=resp.text[:20000], response_json=None, finish_reason=None, usage={})
            if resp.status_code == 429:
                raise RateLimitError(
                    f"LLM provider rate limited us: HTTP 429: {resp.text[:500]}",
                    retry_after=parse_retry_after(resp.headers),
                )
            raise RuntimeError(f"LLM request failed: HTTP {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected LLM response shape: {payload}") from exc
        usage = payload.get("usage") or {}
        finish_reason = choice.get("finish_reason")
        log.info(
            "LLM chat: model=%s in=%d chars out=%d chars tokens=%s/%s finish_reason=%s elapsed=%.1fs",
            self.model,
            input_chars,
            len(content),
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
            finish_reason,
            elapsed,
        )
        if finish_reason == "length":
            log.warning(
                "LLM response was truncated (finish_reason=length, max_tokens=%d), "
                "consider raising llm_max_tokens or shrinking the transcript chunk",
                self.max_tokens,
            )
        log.debug("LLM response:\n%s", content)
        await record(response_text=content, response_json=payload, finish_reason=finish_reason, usage=usage)
        return ChatResult(content=content, finish_reason=finish_reason)

    async def health_check(self) -> None:
        result = await self.chat(
            [{"role": "user", "content": 'Reply with exactly this JSON: {"ok": true}'}]
        )
        parse_json_object(result.content)


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Best-effort recovery for JSON cut off mid-object (e.g. hit max_tokens).

    Walks the text tracking bracket/string nesting, truncates back to the last
    point where a nested object or array was cleanly closed, then closes out
    the remaining open brackets. Returns None if nothing usable is found.
    """
    stack: list[str] = []
    in_string = False
    escape = False
    last_safe_index: int | None = None
    safe_stack: list[str] = []
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            if stack:
                last_safe_index = i + 1
                safe_stack = list(stack)
    if last_safe_index is None:
        return None
    closing = "".join("}" if c == "{" else "]" for c in reversed(safe_stack))
    repaired = text[:last_safe_index].rstrip().rstrip(",") + closing
    try:
        obj = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output, tolerating markdown fences and prose."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace == -1:
            raise ValueError(f"no JSON object in LLM output: {content[:200]}")
        text = text[brace:]
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        repaired = _repair_truncated_json(text)
        if repaired is None:
            raise ValueError(f"could not parse LLM output as JSON: {exc}: {content[:200]}") from exc
        log.warning("LLM output was truncated mid-JSON, salvaged the complete segments from it")
        obj = repaired
    if not isinstance(obj, dict):
        raise ValueError("LLM output was not a JSON object")
    return obj


def parse_segments(content: str) -> list[AdSegment]:
    obj = parse_json_object(content)
    raw = obj.get("segments")
    if raw is None:
        raise ValueError(f'LLM output missing "segments" key: {content[:200]}')
    out: list[AdSegment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        category = str(item.get("category", "ad"))
        if category not in VALID_CATEGORIES:
            category = "ad"
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        out.append(
            AdSegment(
                start=start,
                end=end,
                category=category,
                confidence=max(0.0, min(confidence, 1.0)),
                reason=str(item.get("reason", ""))[:1000],
            )
        )
    return out


async def detect_ads(
    llm: LLMClient,
    transcript: Transcript,
    *,
    system_prompt: str,
    podcast_title: str,
    episode_title: str,
    detection_hints: str | None,
    learned_hints: str | None = None,
    global_learned_hints: str | None = None,
    context_budget_tokens: int,
    cues: list[Cue] | None = None,
    cue_min_prompt_duration_s: float = 2.0,
    log_lines: list[str] | None = None,
    recorder: CallRecorder | None = None,
) -> list[AdSegment]:
    chunks = prompts.chunk_transcript(
        transcript, context_budget_tokens,
        cues=cues, min_cue_duration_s=cue_min_prompt_duration_s,
    )
    if log_lines is not None and len(chunks) > 1:
        log_lines.append(f"transcript exceeds context budget, split into {len(chunks)} overlapping chunks")
    found: list[AdSegment] = []
    for idx, chunk in enumerate(chunks):
        messages = prompts.build_messages(
            system_prompt=system_prompt,
            chunk=chunk,
            podcast_title=podcast_title,
            episode_title=episode_title,
            detection_hints=detection_hints,
            learned_hints=learned_hints,
            global_learned_hints=global_learned_hints,
            has_cues=bool(cues),
        )
        segments = await _detect_chunk(
            llm, messages,
            chunk_label=f"chunk {idx + 1}/{len(chunks)}",
            log_lines=log_lines,
            recorder=recorder,
        )
        if log_lines is not None:
            log_lines.append(f"chunk {idx + 1}/{len(chunks)}: LLM returned {len(segments)} segment(s)")
        found.extend(segments)
    return found


async def _detect_chunk(
    llm: LLMClient,
    messages: list[dict[str, str]],
    *,
    chunk_label: str,
    log_lines: list[str] | None,
    recorder: CallRecorder | None,
) -> list[AdSegment]:
    """One chat call per attempt, retries when the reply has no complete JSON.

    A reply that hit the completion token cap (finish_reason=length) is also
    retried even if the truncated JSON was salvageable, since segments near the
    end may be missing. The salvage is kept as a last resort if every attempt
    truncates.
    """
    salvaged: list[AdSegment] | None = None
    attempt_messages = messages
    error = "no attempt made"
    for attempt in range(1 + MALFORMED_RETRIES):
        result = await llm.chat(attempt_messages, schema=SEGMENTS_SCHEMA, recorder=recorder)
        try:
            segments = parse_segments(result.content)
        except ValueError as exc:
            error = str(exc)
        else:
            if result.finish_reason != "length":
                return segments
            salvaged = segments
            error = "the reply was cut off by the completion token limit (llm_max_tokens)"
        if attempt < MALFORMED_RETRIES:
            note = f"{chunk_label}: malformed LLM output ({error[:200]}), retrying"
            log.warning("%s", note)
            if log_lines is not None:
                log_lines.append(note)
            attempt_messages = messages + [
                {"role": "user", "content": RETRY_NUDGE.format(error=error[:200])}
            ]
    if salvaged is not None:
        note = f"{chunk_label}: every attempt truncated, using {len(salvaged)} salvaged segment(s)"
        log.warning("%s", note)
        if log_lines is not None:
            log_lines.append(note)
        return salvaged
    raise RuntimeError(
        f"LLM returned malformed output for {chunk_label} "
        f"after {1 + MALFORMED_RETRIES} attempt(s): {error[:500]}"
    )
