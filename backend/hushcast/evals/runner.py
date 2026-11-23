"""Run detection over eval episodes.

`run_episode` is the shared core: detect one frozen episode with the
production prompt code and finalize it exactly like the pipeline does
(post-processing, promo check, circuit breaker). The server's eval runs
(jobs.py) and the command line (`run_variant`, below) both go through it.

The command line takes a variant TOML file (settings overrides, optional
extra request params) and caches responses on disk by request hash plus
repeat index, so re-running only pays for calls not made yet.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .. import settings_store
from ..cues.base import cues_from_json
from ..detection import llm as llm_mod
from ..detection.finalize import finalize
from ..errors import RateLimitError
from ..transcription.base import Transcript

REPORT_VERSION = 1
# Settings a variant may not take from a file, only from the environment.
_SECRET_SETTINGS = {"llm_api_key"}
_MAX_TRANSIENT_RETRIES = 5


@dataclass(frozen=True)
class Pricing:
    """Manual USD prices per million tokens. Only a fallback: a cost the
    provider reports always wins."""

    input_per_mtok: float | None = None
    output_per_mtok: float | None = None

    @property
    def complete(self) -> bool:
        return self.input_per_mtok is not None and self.output_per_mtok is not None


@dataclass
class CallStats:
    calls: int = 0
    cached_calls: int = 0
    retries: int = 0  # malformed-output retries (a nudge was appended)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_s: float = 0.0
    reported_cost_usd: float = 0.0
    unreported_calls: int = 0  # answered calls whose provider gave no cost
    responses: list[str] = field(default_factory=list)

    def count(self, entry: dict[str, Any]) -> None:
        """Add one answered call (fresh or cached)."""
        self.prompt_tokens += entry.get("prompt_tokens") or 0
        self.completion_tokens += entry.get("completion_tokens") or 0
        self.elapsed_s += entry.get("elapsed_s") or 0.0
        if entry.get("cost_usd") is None:
            self.unreported_calls += 1
        else:
            self.reported_cost_usd += entry["cost_usd"]
        self.responses.append(entry.get("content") or "")

    def cost(self, pricing: Pricing | None) -> float | None:
        """Cost in USD, None when unknown. 0.0 means known to be free."""
        if self.unreported_calls == 0:
            return self.reported_cost_usd
        if pricing is not None and pricing.complete:
            return (
                self.prompt_tokens * pricing.input_per_mtok + self.completion_tokens * pricing.output_per_mtok
            ) / 1e6
        return None


class EvalClient(llm_mod.LLMClient):
    """LLM client for eval runs: counts usage and cost, and waits out rate
    limits and transient failures instead of failing the episode."""

    def __init__(self, settings: dict[str, Any], *, stats: CallStats):
        super().__init__(settings)
        self.stats = stats

    def _lookup(self, messages: list[dict[str, str]], schema: dict[str, Any] | None) -> dict[str, Any] | None:
        return None

    def _store(self, messages: list[dict[str, str]], schema: dict[str, Any] | None, entry: dict[str, Any]) -> None:
        return None

    async def chat(self, messages, *, schema=None, recorder=None):  # type: ignore[override]
        self.stats.calls += 1
        if len(messages) > 2:
            self.stats.retries += 1
        hit = self._lookup(messages, schema)
        if hit is not None:
            self.stats.cached_calls += 1
            self.stats.count(hit)
            return llm_mod.ChatResult(content=hit["content"], finish_reason=hit["finish_reason"])

        call: dict[str, Any] = {}

        async def capture(recorded: dict[str, Any]) -> None:
            call.update(recorded)

        for attempt in range(_MAX_TRANSIENT_RETRIES + 1):
            try:
                result = await super().chat(messages, schema=schema, recorder=capture)
                break
            except RateLimitError as exc:
                if attempt == _MAX_TRANSIENT_RETRIES:
                    raise
                await asyncio.sleep(exc.retry_after or min(60.0, 5.0 * 2**attempt))
            except (httpx.TransportError, RuntimeError) as exc:
                transient = isinstance(exc, httpx.TransportError) or "HTTP 5" in str(exc)
                if not transient or attempt == _MAX_TRANSIENT_RETRIES:
                    raise
                await asyncio.sleep(min(60.0, 5.0 * 2**attempt))
        entry = {
            "content": result.content,
            "finish_reason": result.finish_reason,
            "prompt_tokens": call.get("prompt_tokens"),
            "completion_tokens": call.get("completion_tokens"),
            "cost_usd": call.get("cost_usd"),
            "elapsed_s": call.get("elapsed_s"),
        }
        self._store(messages, schema, entry)
        self.stats.count(entry)
        return result


class CachingClient(EvalClient):
    def __init__(self, settings: dict[str, Any], *, stats: CallStats, cache_dir: Path, repeat: int):
        super().__init__(settings, stats=stats)
        self.cache_dir = cache_dir
        self.repeat = repeat

    def _path(self, messages: list[dict[str, str]], schema: dict[str, Any] | None) -> Path:
        payload = {
            "url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "extra_params": self.extra_params,
            "schema": schema,
            "messages": messages,
            "repeat": self.repeat,
        }
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _lookup(self, messages, schema):
        path = self._path(messages, schema)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _store(self, messages, schema, entry):
        self._path(messages, schema).write_text(json.dumps(entry), encoding="utf-8")


def current_hints(dataset: dict[str, Any], episode: dict[str, Any]) -> dict[str, str | None]:
    """The hints production would detect this episode with today."""
    feed = (dataset.get("feeds") or {}).get(str(episode.get("feed_id"))) or {}
    return {
        "detection_hints": feed.get("detection_hints"),
        "learned_hints": feed.get("learned_hints"),
        "global_learned_hints": dataset.get("global_learned_hints"),
    }


async def run_episode(
    episode: dict[str, Any],
    repeat: int,
    *,
    settings: dict[str, Any],
    hints: dict[str, str | None],
    make_client: Callable[[CallStats], EvalClient],
    pricing: Pricing | None = None,
) -> dict[str, Any]:
    """Detect one dataset episode once. A failure is a result (outcome
    "error"), not an exception."""
    stats = CallStats()
    client = make_client(stats)
    transcript = Transcript.from_json_dict(episode["transcript"])
    cues = cues_from_json(episode["cues"]) if episode.get("cues") else None
    prompt_cues = cues if (cues and bool(settings["cue_prompt_annotations"])) else None
    duration = float(episode["duration_s"] or transcript.duration)
    run: dict[str, Any] = {"episode_id": episode["episode_id"], "repeat": repeat}
    started = time.monotonic()
    try:
        result = await llm_mod.detect_ads(
            client,
            transcript,
            system_prompt=settings["detection_prompt"],
            podcast_title=episode.get("podcast_title") or "",
            episode_title=episode.get("episode_title") or "",
            detection_hints=hints.get("detection_hints"),
            learned_hints=hints.get("learned_hints"),
            global_learned_hints=hints.get("global_learned_hints"),
            context_budget_tokens=int(settings["llm_context_budget_tokens"]),
            cues=prompt_cues,
            cue_min_prompt_duration_s=float(settings["cue_min_prompt_duration_s"]),
        )
        done = finalize(result, transcript=transcript, cues=cues, duration=duration, settings=settings)
        run.update(
            outcome=done.outcome,
            segments=[[round(s.start, 2), round(s.end, 2), s.category] for s in done.segments],
            error=None,
        )
    except Exception as exc:  # a failed episode is a result, not a crash
        run.update(outcome="error", segments=[], error=f"{type(exc).__name__}: {exc}"[:1000])
    run.update(
        calls=stats.calls,
        cached_calls=stats.cached_calls,
        retries=stats.retries,
        prompt_tokens=stats.prompt_tokens,
        completion_tokens=stats.completion_tokens,
        llm_elapsed_s=round(stats.elapsed_s, 2),
        wall_s=round(time.monotonic() - started, 2),
        cost_usd=stats.cost(pricing),
        responses=stats.responses,
    )
    return run


# ---------------------------------------------------------------------------
# command line: variants, datasets, reports


@dataclass
class Variant:
    name: str
    settings: dict[str, Any]
    extra_params: dict[str, Any] = field(default_factory=dict)
    api_key_env: str | None = None
    pricing: Pricing = Pricing()

    @classmethod
    def load(cls, path: Path) -> Variant:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        settings = dict(data.get("settings") or {})
        if _SECRET_SETTINGS & settings.keys():
            raise ValueError(f"{path}: put the API key in an environment variable and name it in api_key_env")
        unknown = settings.keys() - settings_store.DEFAULTS.keys()
        if unknown:
            raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
        # Without these the run would silently pair production's model name
        # with the default endpoint.
        missing = [k for k in ("llm_base_url", "llm_model") if not settings.get(k)]
        if missing:
            raise ValueError(f"{path}: [settings] must set {' and '.join(missing)}")
        pricing = data.get("pricing") or {}
        return cls(
            name=str(data.get("name") or path.stem),
            settings=settings,
            extra_params=dict(data.get("extra_params") or {}),
            api_key_env=data.get("api_key_env"),
            pricing=Pricing(pricing.get("input_per_mtok"), pricing.get("output_per_mtok")),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "settings": self.settings,
            "extra_params": self.extra_params,
            "pricing": {
                "input_per_mtok": self.pricing.input_per_mtok,
                "output_per_mtok": self.pricing.output_per_mtok,
            },
        }


def load_dataset(source: str, cookie: str | None = None) -> dict[str, Any]:
    """A dataset file, or a hushcast base URL to fetch the export from (GET)."""
    if source.startswith(("http://", "https://")):
        url = source.rstrip("/")
        if not url.endswith("/api/evals/export"):
            url += "/api/evals/export"
        headers = {"Cookie": cookie} if cookie else {}
        resp = httpx.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _settings_for(dataset: dict[str, Any], variant: Variant) -> dict[str, Any]:
    settings = settings_store.merge_settings({**(dataset.get("settings") or {}), **variant.settings})
    if variant.api_key_env:
        key = os.environ.get(variant.api_key_env)
        if not key:
            raise ValueError(f"environment variable {variant.api_key_env} is not set")
        settings["llm_api_key"] = key
    return settings


async def run_variant(
    dataset: dict[str, Any],
    variant: Variant,
    *,
    repeats: int,
    concurrency: int,
    hints_mode: str,
    cache_dir: Path,
    episode_ids: set[int] | None = None,
    dataset_source: str = "",
) -> dict[str, Any]:
    settings = _settings_for(dataset, variant)
    episodes = [e for e in dataset["episodes"] if episode_ids is None or e["episode_id"] in episode_ids]
    cache_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(episodes) * repeats
    done_count = 0

    async def task(ep: dict[str, Any], repeat: int) -> dict[str, Any]:
        nonlocal done_count

        def make_client(stats: CallStats) -> EvalClient:
            client = CachingClient(settings, stats=stats, cache_dir=cache_dir, repeat=repeat)
            client.extra_params = variant.extra_params
            return client

        async with sem:
            run = await run_episode(
                ep,
                repeat,
                settings=settings,
                hints=current_hints(dataset, ep) if hints_mode == "current" else {},
                make_client=make_client,
                pricing=variant.pricing,
            )
        run.pop("responses", None)  # the cache already holds them
        done_count += 1
        note = f" ({run['error'][:80]})" if run["error"] else ""
        print(
            f"[{done_count}/{total}] episode {ep['episode_id']} repeat {repeat + 1}: {run['outcome']}{note}",
            file=sys.stderr,
        )
        return run

    runs = await asyncio.gather(*(task(ep, r) for r in range(repeats) for ep in episodes))
    return {
        "report_version": REPORT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "variant": variant.describe(),
        "effective_settings": {k: settings[k] for k in sorted(settings) if k in REPORTED_SETTINGS},
        "hints_mode": hints_mode,
        "repeats": repeats,
        "dataset": {
            "source": dataset_source,
            "exported_at": dataset.get("exported_at"),
            "episodes": len(episodes),
        },
        "episodes": {
            str(e["episode_id"]): {
                "podcast_title": e.get("podcast_title"),
                "episode_title": e.get("episode_title"),
                "duration_s": e["duration_s"],
                "promo": e["promo"],
                "truth": [[t["start"], t["end"], t["category"]] for t in e["truth"]],
                "optional": e.get("optional") or [],
                "transcript_source": e.get("transcript_source"),
                # corrections on it may have been distilled into the hints this run used
                "hints_leak": hints_mode == "current" and bool(e.get("hints_leak")),
            }
            for e in episodes
        },
        "runs": sorted(runs, key=lambda r: (r["episode_id"], r["repeat"])),
    }


# Recorded with a run so a result can be traced back to its configuration.
REPORTED_SETTINGS = {
    "llm_base_url",
    "llm_model",
    "llm_temperature",
    "llm_context_budget_tokens",
    "llm_max_tokens",
    "snap_tolerance_s",
    "min_duration_s",
    "merge_gap_s",
    "refine_window_s",
    "refine_min_gap_s",
    "cue_prompt_annotations",
    "cue_min_prompt_duration_s",
    "cue_bridge_max_gap_s",
    "cue_edge_max_extension_s",
}
