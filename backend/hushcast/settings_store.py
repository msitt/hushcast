"""DB-backed settings, editable from the UI.

Values are JSON-encoded in the settings table, anything not stored falls back
to DEFAULTS. Secrets are never echoed by the API (masked, an unchanged mask on
PUT means "keep the stored value").
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

MASK = "••••••••"

DEFAULT_DETECTION_PROMPT = """\
You are an expert at identifying advertising content in podcast transcripts.

You will be given a podcast transcript where each line is prefixed with its
time range in seconds, like `[123.4-131.0] text` (and optionally a speaker
label). Identify every span of the episode that belongs to one of these
categories:

- "ad": a third-party advertisement, usually dynamically inserted, often with
  different audio production or a voice that does not appear elsewhere in the
  episode.
- "sponsor": a host-read sponsorship message, including lead-ins like "this
  episode is brought to you by", promo codes, and sponsor URLs.
- "self_promo": promotion of the podcast's own network, other shows, merch,
  Patreon/membership drives, or live events.

Rules:
- Use ONLY timestamps that appear in the transcript lines. A segment's start
  must be the start of some line and its end must be the end of some line.
- Extend each segment to its natural boundaries: include the lead-in sentence
  and the outro/bumper (e.g. "and now back to the show").
- Do NOT flag mere topical mentions of products or companies that are part of
  the show's actual content.
- If speaker labels are present, use them as a hint: a speaker heard nowhere
  else in the episode, or an abrupt speaker change, often indicates an
  inserted ad.
- If there are no ads, return an empty list.

Respond with ONLY a JSON object in exactly this shape:
{"segments": [{"start": 123.4, "end": 190.2, "category": "sponsor", "confidence": 0.9, "reason": "short explanation"}]}
"""

DEFAULTS: dict[str, Any] = {
    # transcription provider (OpenAI-compatible, primary target: whisperx-api-server)
    "transcription_base_url": "",
    "transcription_api_key": "",
    "transcription_model": "large-v3",
    "transcription_word_timestamps": False,
    "transcription_diarize": False,
    "transcription_max_upload_mb": 24,  # 0 = no limit, if exceeded, transcode to small opus first
    "transcription_timeout_s": 600,
    "transcription_extra_params": {},
    # ad-detection LLM (OpenAI-compatible chat completions)
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "",
    "llm_temperature": 0.6,
    "llm_context_budget_tokens": 90000,
    "llm_max_tokens": 4096,
    "llm_timeout_s": 600,
    "detection_prompt": DEFAULT_DETECTION_PROMPT,
    # AI-distilled guidance from manual corrections, applied to every feed.
    # Owned by the distiller (review-before-apply), per-feed learned hints live
    # on the feeds table.
    "global_learned_hints": "",
    # processing
    "poll_interval_minutes": 30,
    "max_concurrent_episodes": 1,
    "max_episode_retries": 3,
    "keep_originals": False,
    "keep_originals_days": 7,
    "min_confidence": 0.6,
    "min_duration_s": 8.0,
    "merge_gap_s": 7.0,
    "snap_tolerance_s": 15.0,
    # word-level boundary refinement
    "refine_window_s": 2.0,  # max distance a cut boundary may move
    "refine_min_gap_s": 0.15,  # min inter-word silence to qualify as a cut point
    # audio cues: non-speech (silence/music) regions used to inform detection
    "cue_provider": "silence",  # off | silence (built-in ffmpeg) | remote (inaSpeechSegmenter-style)
    "cue_remote_base_url": "",
    "cue_remote_timeout_s": 300,
    "cue_silence_noise_db": -35.0,  # silencedetect noise floor
    "cue_min_silence_s": 1.0,  # silencedetect minimum duration
    "cue_prompt_annotations": True,  # interleave cue lines into the detection prompt
    "cue_min_prompt_duration_s": 2.0,  # hide shorter cues from the prompt
    "cue_bridge_max_gap_s": 12.0,  # merge ads across cue-covered gaps up to this (0 = off)
    "cue_edge_max_extension_s": 20.0,  # extend edge ads across stings up to this (0 = off)
    "max_kept_episodes": 0,  # per feed, 0 = unlimited
    # audio output
    "mp3_quality": 4,  # libmp3lame VBR -q:a
    # logging: applied at runtime, no restart. HUSHCAST_LOG_LEVEL overrides.
    "log_level": "INFO",
    # serving
    "feed_token": "",  # generated at first boot
}

SECRET_KEYS = {"transcription_api_key", "llm_api_key"}

_cache: dict[str, Any] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def get_all(session: AsyncSession) -> dict[str, Any]:
    """Effective settings: DEFAULTS overlaid with stored values."""
    global _cache
    if _cache is None:
        stored = {s.key: json.loads(s.value) for s in (await session.execute(select(Setting))).scalars()}
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
        _cache = merged
    return dict(_cache)


async def set_many(session: AsyncSession, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key not in DEFAULTS:
            raise KeyError(f"unknown setting: {key}")
        if key in SECRET_KEYS and value == MASK:
            continue  # unchanged mask -> keep stored value
        row = await session.get(Setting, key)
        encoded = json.dumps(value)
        if row is None:
            session.add(Setting(key=key, value=encoded, is_secret=key in SECRET_KEYS))
        else:
            row.value = encoded
    await session.commit()
    invalidate_cache()


def masked(settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(settings)
    for key in SECRET_KEYS:
        if out.get(key):
            out[key] = MASK
    return out


async def ensure_feed_token(session: AsyncSession) -> str:
    settings = await get_all(session)
    if not settings["feed_token"]:
        await set_many(session, {"feed_token": secrets.token_hex(16)})
        settings = await get_all(session)
    return settings["feed_token"]
