"""Eval labels on episodes, the eval set overview, and the dataset export."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..detection.llm import LLMClient
from ..evals import jobs, labels, results
from ..evals.scoring import EDGE_GAP_MAX_S, ScoreConfig
from ..evals.truth import SegmentRow, effective_truth
from ..models import Episode, EvalLabel, EvalModel, EvalResult, EvalRun, Feed, Segment
from ..pipeline import state
from .deps import get_session

router = APIRouter(prefix="/api", tags=["evals"])

# Detection has finished and its segments are final (until a human or a
# re-detect changes them).
DETECTED_STATUSES = (state.PROCESSED, state.EXPIRED, state.REVIEW)


class EvalLabelPut(BaseModel):
    verdict: Literal["verified", "excluded"]
    note: str | None = None


@router.put("/episodes/{episode_id}/eval-label")
async def put_eval_label(
    episode_id: int, body: EvalLabelPut, session: AsyncSession = Depends(get_session)
) -> dict:
    ep = await session.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(404, "episode not found")
    if ep.status in state.ACTIVE or ep.status == state.QUEUED:
        raise HTTPException(409, "episode is currently queued/processing, label it once it settles")
    if body.verdict == "verified" and ep.status not in DETECTED_STATUSES:
        # skipped covers dropped promo-only episodes, which do have segments.
        # Anything else without segments never finished detection, and saving
        # it would record "no ads" as the truth.
        has_segments = (
            await session.execute(select(func.count()).where(Segment.episode_id == episode_id))
        ).scalar_one()
        if ep.status != state.SKIPPED or not has_segments:
            raise HTTPException(409, f"episode is {ep.status}, only an episode that finished detection can be verified")
    try:
        label = await labels.save_label(session, ep, body.verdict, body.note)
    except labels.LabelError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    await session.refresh(label)
    return labels.label_out(label, await labels.current_truth(session, episode_id))


@router.delete("/episodes/{episode_id}/eval-label", status_code=204)
async def delete_eval_label(episode_id: int, session: AsyncSession = Depends(get_session)) -> None:
    await session.execute(delete(EvalLabel).where(EvalLabel.episode_id == episode_id))
    await session.commit()


@router.get("/evals/summary")
async def evals_summary(session: AsyncSession = Depends(get_session)) -> dict:
    counts = dict(
        (await session.execute(select(EvalLabel.verdict, func.count()).group_by(EvalLabel.verdict))).all()
    )
    return {"verified": counts.get("verified", 0), "excluded": counts.get("excluded", 0)}


@router.get("/evals/labels")
async def evals_labels(session: AsyncSession = Depends(get_session)) -> dict:
    """The eval set at a glance: every label, and coverage per podcast."""
    settings = await settings_store.get_all(session)
    global_hints = settings.get("global_learned_hints") or None
    feeds = {f.id: f for f in (await session.execute(select(Feed))).scalars()}
    rows = (
        await session.execute(
            select(EvalLabel, Episode)
            .join(Episode, EvalLabel.episode_id == Episode.id)
            .order_by(EvalLabel.updated_at.desc())
        )
    ).all()
    segments: dict[int, list[SegmentRow]] = defaultdict(list)
    if rows:
        for s in (
            await session.execute(
                select(Segment).where(Segment.episode_id.in_([label.episode_id for label, _ in rows]))
            )
        ).scalars():
            segments[s.episode_id].append(SegmentRow(s.start_s, s.end_s, s.category, s.kept, s.source))

    # unlabeled episodes with a finished detection: what is left to label
    labeled = select(EvalLabel.episode_id)
    candidates = dict(
        (
            await session.execute(
                select(Episode.feed_id, func.count())
                .where(Episode.status.in_(DETECTED_STATUSES), Episode.id.notin_(labeled))
                .group_by(Episode.feed_id)
            )
        ).all()
    )

    per_feed: dict[int, dict] = {
        f.id: {"feed_id": f.id, "title": f.title, "verified": 0, "excluded": 0, "verified_s": 0.0,
               "candidates": candidates.get(f.id, 0)}
        for f in feeds.values()
        if not f.whitelisted
    }
    out = []
    for label, ep in rows:
        feed = feeds.get(ep.feed_id)
        truth = json.loads(label.truth_json) if label.truth_json else []
        verified = label.verdict == "verified"
        out.append({
            "episode_id": ep.id,
            "episode_title": ep.title,
            "feed_id": ep.feed_id,
            "feed_title": feed.title if feed else "",
            "published_at": ep.published_at,
            "verdict": label.verdict,
            "note": label.note,
            "promo": label.promo,
            "ad_ranges": len(truth) if verified and not label.promo else None,
            "ad_s": sum(t["end"] - t["start"] for t in truth) if verified and not label.promo else None,
            "optional_s": sum(o[1] - o[0] for o in labels.label_optional(label))
            if verified and not label.promo
            else None,
            "duration_s": label.duration_s if verified else ep.duration_s,
            "transcript_source": label.transcript_source,
            "matches_segments": labels.truth_matches(label, effective_truth(segments.get(ep.id, []))),
            "hints_leak": verified and labels.hints_leak(label, feed, global_hints),
            "updated_at": label.updated_at,
        })
        bucket = per_feed.get(ep.feed_id)
        if bucket is not None:
            bucket[label.verdict] += 1
            if verified:
                bucket["verified_s"] += label.duration_s or 0.0
    return {
        "verified": sum(1 for o in out if o["verdict"] == "verified"),
        "excluded": sum(1 for o in out if o["verdict"] == "excluded"),
        "verified_s": sum(o["duration_s"] or 0.0 for o in out if o["verdict"] == "verified"),
        "feeds": sorted(per_feed.values(), key=lambda b: b["title"].lower()),
        "labels": out,
    }


@router.get("/evals/export")
async def evals_export(session: AsyncSession = Depends(get_session)) -> dict:
    """Every verified label with its snapshot, the input for the eval runner."""
    return await labels.export_dataset(session)


# ---------------------------------------------------------------------------
# models to evaluate


class EvalModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1)
    base_url: str | None = None
    # None leaves a stored key alone, an empty string clears it
    api_key: str | None = None
    temperature: float | None = None
    extra_params: dict | None = None
    input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float | None = Field(default=None, ge=0)


def _model_out(m: EvalModel, settings: dict) -> dict:
    url, _ = jobs.resolve_connection(settings, m)
    return {
        "id": m.id,
        "name": m.name,
        "model": m.model,
        "base_url": m.base_url,
        "has_api_key": bool(m.api_key),
        "temperature": m.temperature,
        "extra_params": jobs.model_extra_params(m) or None,
        "input_per_mtok": m.input_per_mtok,
        "output_per_mtok": m.output_per_mtok,
        "effective_base_url": url,  # where calls for this model actually go
    }


def _apply_model(m: EvalModel, body: EvalModelIn) -> None:
    m.name = body.name.strip()
    m.model = body.model.strip()
    m.base_url = (body.base_url or "").strip().rstrip("/") or None
    if body.api_key is not None:
        m.api_key = body.api_key.strip() or None
    m.temperature = body.temperature
    m.extra_params_json = json.dumps(body.extra_params) if body.extra_params else None
    m.input_per_mtok = body.input_per_mtok
    m.output_per_mtok = body.output_per_mtok


async def _name_taken(session: AsyncSession, name: str, exclude_id: int | None = None) -> bool:
    q = select(EvalModel.id).where(func.lower(EvalModel.name) == name.strip().lower())
    if exclude_id is not None:
        q = q.where(EvalModel.id != exclude_id)
    return (await session.execute(q)).first() is not None


@router.get("/evals/models")
async def list_eval_models(session: AsyncSession = Depends(get_session)) -> list[dict]:
    settings = await settings_store.get_all(session)
    rows = (await session.execute(select(EvalModel).order_by(func.lower(EvalModel.name)))).scalars()
    return [_model_out(m, settings) for m in rows]


@router.post("/evals/models", status_code=201)
async def create_eval_model(body: EvalModelIn, session: AsyncSession = Depends(get_session)) -> dict:
    if await _name_taken(session, body.name):
        raise HTTPException(409, "a model with this name already exists")
    m = EvalModel(name="", model="")
    _apply_model(m, body)
    session.add(m)
    await session.commit()
    return _model_out(m, await settings_store.get_all(session))


@router.put("/evals/models/{model_id}")
async def update_eval_model(
    model_id: int, body: EvalModelIn, session: AsyncSession = Depends(get_session)
) -> dict:
    m = await session.get(EvalModel, model_id)
    if m is None:
        raise HTTPException(404, "model not found")
    if await _name_taken(session, body.name, exclude_id=model_id):
        raise HTTPException(409, "a model with this name already exists")
    _apply_model(m, body)
    await session.commit()
    return _model_out(m, await settings_store.get_all(session))


@router.get("/evals/models/{model_id}/api-key")
async def reveal_eval_model_key(model_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """The model's stored key in plain text, fetched only when the user asks to see it."""
    m = await session.get(EvalModel, model_id)
    if m is None:
        raise HTTPException(404, "model not found")
    return {"value": m.api_key or ""}


@router.delete("/evals/models/{model_id}", status_code=204)
async def delete_eval_model(model_id: int, session: AsyncSession = Depends(get_session)) -> None:
    busy = (
        await session.execute(
            select(func.count()).where(EvalRun.model_id == model_id, EvalRun.status.in_(jobs.UNFINISHED))
        )
    ).scalar_one()
    if busy:
        raise HTTPException(409, "this model has a run in progress, cancel it first")
    # explicit: SQLite only applies SET NULL when foreign keys are on for the connection
    await session.execute(update(EvalRun).where(EvalRun.model_id == model_id).values(model_id=None))
    await session.execute(delete(EvalModel).where(EvalModel.id == model_id))
    await session.commit()


@router.post("/evals/models/{model_id}/test")
async def test_eval_model(model_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    m = await session.get(EvalModel, model_id)
    if m is None:
        raise HTTPException(404, "model not found")
    try:
        client = LLMClient(jobs.run_settings(await settings_store.get_all(session), m))
        client.extra_params = jobs.model_extra_params(m)
        await client.health_check()
        return {"ok": True, "message": "model responded with valid JSON"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/evals/models/{model_id}/promote")
async def promote_eval_model(model_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Make this model the one production detection uses."""
    m = await session.get(EvalModel, model_id)
    if m is None:
        raise HTTPException(404, "model not found")
    changes: dict = {"llm_model": m.model}
    if m.temperature is not None:
        changes["llm_temperature"] = m.temperature
    if m.base_url:
        # The model lives on its own endpoint, so production has to follow it
        # there. The eval connection's key is deliberately never copied: it
        # exists to keep eval spend separate.
        changes["llm_base_url"] = m.base_url
        changes["llm_api_key"] = m.api_key or ""
    await settings_store.set_many(session, changes)
    return {
        "ok": True,
        "changed": sorted(changes),
        # production has no setting for these, so they can't follow the model
        "extra_params_not_applied": bool(jobs.model_extra_params(m)),
    }


# ---------------------------------------------------------------------------
# runs


class EvalRunsIn(BaseModel):
    model_ids: list[int] = Field(min_length=1)
    repeats: int = Field(default=3, ge=1, le=10)
    hints_mode: Literal["current", "none"] = "current"


def _run_out(run: EvalRun, progress: dict) -> dict:
    return {
        "id": run.id,
        "model_id": run.model_id,
        "name": run.name,
        "model": run.model,
        "hints_mode": run.hints_mode,
        "repeats": run.repeats,
        "total": run.total,
        "status": run.status,
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        **progress,
    }


@router.get("/evals/runs")
async def list_eval_runs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    runs = list((await session.execute(select(EvalRun).order_by(EvalRun.id.desc()))).scalars())
    progress = await results.run_progress(session, [r.id for r in runs])
    return [_run_out(r, progress[r.id]) for r in runs]


@router.get("/evals/runs/estimate")
async def estimate_eval_run(
    repeats: int = Query(3, ge=1, le=10), session: AsyncSession = Depends(get_session)
) -> dict:
    """What one model's run would involve. Tokens only: prices live with the provider."""
    dataset = await labels.export_dataset(session)
    return {
        "episodes": len(dataset["episodes"]),
        "detections": len(dataset["episodes"]) * repeats,
        "input_tokens": jobs.estimate_input_tokens(dataset, repeats),
    }


@router.post("/evals/runs", status_code=201)
async def start_eval_runs(body: EvalRunsIn, session: AsyncSession = Depends(get_session)) -> list[dict]:
    verified = (
        await session.execute(select(func.count()).where(EvalLabel.verdict == "verified"))
    ).scalar_one()
    if not verified:
        raise HTTPException(409, "there are no verified episodes to run on")
    models = {
        m.id: m
        for m in (await session.execute(select(EvalModel).where(EvalModel.id.in_(body.model_ids)))).scalars()
    }
    missing = [i for i in body.model_ids if i not in models]
    if missing:
        raise HTTPException(404, f"model not found: {missing}")
    settings = await settings_store.get_all(session)
    created = []
    for model_id in dict.fromkeys(body.model_ids):
        try:
            jobs.run_settings(settings, models[model_id])
        except jobs.RunConfigError as exc:
            raise HTTPException(409, f"{models[model_id].name}: {exc}") from exc
        run = await jobs.create_run(session, models[model_id], repeats=body.repeats, hints_mode=body.hints_mode)
        run.total = verified * body.repeats
        created.append(run)
    await session.commit()
    jobs.manager.kick()
    progress = await results.run_progress(session, [r.id for r in created])
    return [_run_out(r, progress[r.id]) for r in created]


@router.post("/evals/runs/{run_id}/cancel")
async def cancel_eval_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status not in jobs.UNFINISHED:
        raise HTTPException(409, f"run is {run.status}, nothing to cancel")
    await jobs.manager.cancel(run_id)
    return {"ok": True}


@router.post("/evals/runs/{run_id}/resume")
async def resume_eval_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status not in jobs.RESUMABLE:
        raise HTTPException(409, f"run is {run.status}, nothing to resume")
    run.status = "queued"
    run.error = None
    await session.commit()
    jobs.manager.kick()
    return {"ok": True}


@router.delete("/evals/runs/{run_id}", status_code=204)
async def delete_eval_run(run_id: int, session: AsyncSession = Depends(get_session)) -> None:
    run = await session.get(EvalRun, run_id)
    if run is None:
        return
    if run.status in jobs.UNFINISHED:
        raise HTTPException(409, "cancel the run before deleting it")
    await session.execute(delete(EvalResult).where(EvalResult.run_id == run_id))
    await session.delete(run)
    await session.commit()


@router.get("/evals/results")
async def eval_results(
    runs: str = Query(..., description="comma-separated run ids"),
    content_weight: float = Query(3.0, ge=0, le=100),
    collar: float = Query(1.0, ge=0, le=30),
    blunder: float = Query(5.0, ge=0, le=600),
    edge_gap: float = Query(EDGE_GAP_MAX_S, ge=0, le=300),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        run_ids = [int(x) for x in runs.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(400, "runs must be comma-separated ids") from exc
    cfg = ScoreConfig(collar_s=collar, content_weight=content_weight, blunder_s=blunder, edge_gap_s=edge_gap)
    return await results.scored_runs(session, run_ids, cfg)
