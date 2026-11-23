"""Eval runs inside the server: one model over the verified eval set.

Runs execute one at a time in a background task, a couple of episodes
concurrently, so they share the LLM provider with the real pipeline without
starving it. Every episode detection is stored as it finishes, which makes a
run resumable: resuming only does the detections that are missing.

A run never restarts on its own. After a server restart, unfinished runs are
marked interrupted and wait for a Resume, because a run spends money.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..db import session_factory
from ..detection import prompts
from ..models import EvalModel, EvalResult, EvalRun
from ..transcription.base import Transcript
from . import labels, runner

log = logging.getLogger(__name__)

EVAL_CONCURRENCY = 2
UNFINISHED = ("queued", "running")
RESUMABLE = ("cancelled", "interrupted", "failed")

# Settings that change what a detection produces, apart from the model itself.
_FINGERPRINT_SETTINGS = tuple(
    k for k in labels.DETECTION_SETTINGS if k not in ("llm_model", "llm_temperature")
)


class RunConfigError(Exception):
    """The run can't start as configured, the message says why."""


def resolve_connection(settings: dict[str, Any], model: EvalModel) -> tuple[str, str]:
    """(base_url, api_key) for a model: production, overridden by the eval
    connection, overridden by the model.

    A level that names a different endpoint brings its own key or none at
    all. Falling back to a key from another level would send that key to a
    provider it was never meant for.
    """
    url, key = str(settings["llm_base_url"] or ""), str(settings["llm_api_key"] or "")
    for level_url, level_key in (
        (settings.get("eval_llm_base_url"), settings.get("eval_llm_api_key")),
        (model.base_url, model.api_key),
    ):
        if level_url and level_url.rstrip("/") != url.rstrip("/"):
            url, key = level_url, level_key or ""
        elif level_key:
            key = level_key
    return url, key


def run_settings(settings: dict[str, Any], model: EvalModel) -> dict[str, Any]:
    out = dict(settings)
    out["llm_base_url"], out["llm_api_key"] = resolve_connection(settings, model)
    out["llm_model"] = model.model
    if model.temperature is not None:
        out["llm_temperature"] = model.temperature
    if not out["llm_base_url"]:
        raise RunConfigError("no LLM endpoint is configured for evals")
    return out


def model_extra_params(model: EvalModel) -> dict[str, Any]:
    if not model.extra_params_json:
        return {}
    try:
        data = json.loads(model.extra_params_json)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def fingerprint(dataset: dict[str, Any], hints_mode: str) -> str:
    """Identifies the conditions a run was made under, apart from the model.
    Two runs are only a fair comparison when these match."""
    payload = {
        "settings": {k: dataset["settings"].get(k) for k in _FINGERPRINT_SETTINGS},
        "hints_mode": hints_mode,
        "hints": {"global": dataset.get("global_learned_hints"), "feeds": dataset.get("feeds")}
        if hints_mode == "current"
        else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def estimate_input_tokens(dataset: dict[str, Any], repeats: int) -> int:
    """Rough prompt tokens for one model over the dataset. Output tokens are
    small next to this, and reasoning tokens can't be predicted."""
    system = prompts.estimate_tokens(str(dataset["settings"].get("detection_prompt") or ""))
    budget = int(dataset["settings"].get("llm_context_budget_tokens") or 0) or 1
    total = 0
    for ep in dataset["episodes"]:
        transcript = Transcript.from_json_dict(ep["transcript"])
        body = prompts.estimate_tokens("\n".join(prompts.render_lines(transcript)))
        chunks = max(1, -(-body // budget))
        # each chunk repeats the system prompt and roughly 500 tokens of framing and hints
        total += body + chunks * (system + 500)
    return total * repeats


class EvalRunManager:
    def __init__(self) -> None:
        self._loop_task: asyncio.Task | None = None
        self._run_task: asyncio.Task | None = None
        self._current: int | None = None
        self._wake = asyncio.Event()
        self._cancel_requested: set[int] = set()
        self._stopping = False

    async def start(self) -> None:
        async with session_factory()() as session:
            await session.execute(
                update(EvalRun).where(EvalRun.status.in_(UNFINISHED)).values(status="interrupted")
            )
            await session.commit()
        self._stopping = False
        # created here, not in __init__: an Event binds to the loop that first
        # waits on it, and the app can be started more than once per process
        self._wake = asyncio.Event()
        self._cancel_requested.clear()
        self._loop_task = asyncio.create_task(self._loop(), name="eval-runs")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._run_task, self._loop_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._run_task, self._loop_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._run_task = self._loop_task = None

    def kick(self) -> None:
        self._wake.set()

    async def cancel(self, run_id: int) -> None:
        self._cancel_requested.add(run_id)
        if self._current == run_id and self._run_task is not None:
            self._run_task.cancel()
            return
        async with session_factory()() as session:
            await session.execute(
                update(EvalRun)
                .where(EvalRun.id == run_id, EvalRun.status.in_(UNFINISHED))
                .values(status="cancelled", finished_at=datetime.now(UTC))
            )
            await session.commit()

    async def _loop(self) -> None:
        while True:
            async with session_factory()() as session:
                run_id = (
                    await session.execute(
                        select(EvalRun.id).where(EvalRun.status == "queued").order_by(EvalRun.id).limit(1)
                    )
                ).scalar_one_or_none()
            if run_id is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            self._current = run_id
            self._run_task = asyncio.create_task(self._execute(run_id), name=f"eval-run-{run_id}")
            try:
                await self._run_task
            except asyncio.CancelledError:
                if self._stopping:
                    raise
            except Exception:
                log.exception("eval run %s crashed", run_id)
            finally:
                self._current = None
                self._run_task = None

    async def _set_status(self, run_id: int, status: str, *, error: str | None = None, **values: Any) -> None:
        async with session_factory()() as session:
            await session.execute(
                update(EvalRun).where(EvalRun.id == run_id).values(status=status, error=error, **values)
            )
            await session.commit()

    async def _execute(self, run_id: int) -> None:
        factory = session_factory()
        try:
            async with factory() as session:
                run = await session.get(EvalRun, run_id)
                if run is None or run.status != "queued":
                    return
                model = await session.get(EvalModel, run.model_id) if run.model_id else None
                if model is None:
                    raise RunConfigError("the model entry for this run was deleted")
                settings = run_settings(await settings_store.get_all(session), model)
                extra_params = model_extra_params(model)
                pricing = runner.Pricing(model.input_per_mtok, model.output_per_mtok)
                dataset = await labels.export_dataset(session)
                if not dataset["episodes"]:
                    raise RunConfigError("there are no verified episodes to run on")
                mark = fingerprint(dataset, run.hints_mode)
                if run.fingerprint and run.fingerprint != mark:
                    raise RunConfigError(
                        "the hints or detection settings changed since this run started. "
                        "Its results can't be completed under the same conditions, start a new run"
                    )
                done = set(
                    (
                        await session.execute(
                            select(EvalResult.episode_id, EvalResult.repeat).where(EvalResult.run_id == run_id)
                        )
                    ).all()
                )
                run.fingerprint = mark
                run.total = len(dataset["episodes"]) * run.repeats
                run.status = "running"
                run.error = None
                run.started_at = run.started_at or datetime.now(UTC)
                run.finished_at = None
                hints_mode, repeats = run.hints_mode, run.repeats
                await session.commit()
        except RunConfigError as exc:
            await self._set_status(run_id, "failed", error=str(exc), finished_at=datetime.now(UTC))
            return

        sem = asyncio.Semaphore(EVAL_CONCURRENCY)

        def make_client(stats: runner.CallStats) -> runner.EvalClient:
            client = runner.EvalClient(settings, stats=stats)
            client.extra_params = extra_params
            return client

        async def one(ep: dict[str, Any], repeat: int) -> None:
            async with sem:
                result = await runner.run_episode(
                    ep,
                    repeat,
                    settings=settings,
                    hints=runner.current_hints(dataset, ep) if hints_mode == "current" else {},
                    make_client=make_client,
                    pricing=pricing,
                )
                async with factory() as session:
                    session.add(
                        EvalResult(
                            run_id=run_id,
                            episode_id=ep["episode_id"],
                            repeat=repeat,
                            outcome=result["outcome"],
                            segments_json=json.dumps(result["segments"]),
                            error=result["error"],
                            calls=result["calls"],
                            retries=result["retries"],
                            prompt_tokens=result["prompt_tokens"],
                            completion_tokens=result["completion_tokens"],
                            cost_usd=result["cost_usd"],
                            llm_elapsed_s=result["llm_elapsed_s"],
                            inputs_hash=ep.get("inputs_hash"),
                            responses_json=json.dumps(result["responses"]),
                        )
                    )
                    await session.commit()

        pending = [
            (ep, r) for r in range(repeats) for ep in dataset["episodes"] if (ep["episode_id"], r) not in done
        ]
        tasks = [asyncio.create_task(one(ep, r)) for ep, r in pending]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            status = "cancelled" if run_id in self._cancel_requested else "interrupted"
            self._cancel_requested.discard(run_id)
            await self._set_status(run_id, status, finished_at=datetime.now(UTC))
            raise
        except Exception as exc:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._set_status(
                run_id, "failed", error=f"{type(exc).__name__}: {exc}"[:1000], finished_at=datetime.now(UTC)
            )
            return
        await self._set_status(run_id, "done", finished_at=datetime.now(UTC))


manager = EvalRunManager()


async def create_run(session: AsyncSession, model: EvalModel, *, repeats: int, hints_mode: str) -> EvalRun:
    run = EvalRun(
        model_id=model.id,
        name=model.name,
        model=model.model,
        config_json=json.dumps({
            "base_url": model.base_url,
            "temperature": model.temperature,
            "extra_params": model_extra_params(model),
            "input_per_mtok": model.input_per_mtok,
            "output_per_mtok": model.output_per_mtok,
        }),
        hints_mode=hints_mode,
        repeats=repeats,
        status="queued",
    )
    session.add(run)
    return run
