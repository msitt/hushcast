"""Score stored eval runs against the current labels.

Runs keep predictions, not scores, so everything here is recomputed on
request: correcting a label re-scores every run, and the weighting can change
freely. A result only counts while the transcript and cues it ran on are still
the label's (inputs_hash), otherwise the prediction is for inputs that no
longer exist and the episode is left out of that run.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..models import Episode, EvalLabel, EvalResult, EvalRun, Feed
from . import labels, summarize
from .scoring import ScoreConfig, score_episode


async def run_progress(session: AsyncSession, run_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per run: detections finished, how many failed, tokens and cost so far,
    and how much of today's verified set the run has usable results for."""
    verified = (
        await session.execute(select(func.count()).where(EvalLabel.verdict == "verified"))
    ).scalar_one()
    out: dict[int, dict[str, Any]] = {
        rid: {"done": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "cost_unknown": 0,
              "covered": 0, "verified": verified}
        for rid in run_ids
    }
    if not run_ids:
        return out
    # the same rule scored_runs applies: the episode is still verified and the
    # result ran on the label's current transcript and cues
    covered = dict(
        (
            await session.execute(
                select(EvalResult.run_id, func.count(func.distinct(EvalResult.episode_id)))
                .join(EvalLabel, EvalLabel.episode_id == EvalResult.episode_id)
                .where(
                    EvalResult.run_id.in_(run_ids),
                    EvalLabel.verdict == "verified",
                    EvalResult.inputs_hash == EvalLabel.inputs_hash,
                )
                .group_by(EvalResult.run_id)
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(
                EvalResult.run_id,
                func.count(),
                func.sum(case((EvalResult.outcome == "error", 1), else_=0)),
                func.coalesce(func.sum(EvalResult.prompt_tokens), 0),
                func.coalesce(func.sum(EvalResult.completion_tokens), 0),
                func.coalesce(func.sum(EvalResult.cost_usd), 0.0),
                func.sum(case((EvalResult.cost_usd.is_(None), 1), else_=0)),
            )
            .where(EvalResult.run_id.in_(run_ids))
            .group_by(EvalResult.run_id)
        )
    ).all()
    for rid, done, errors, p_tok, c_tok, cost, unknown in rows:
        out[rid] = {
            "done": done,
            "errors": errors or 0,
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            # None when any detection has no cost, 0.0 is a real (free) total
            "cost_usd": None if unknown else float(cost),
            "cost_unknown": unknown or 0,
            "covered": covered.get(rid, 0),
            "verified": verified,
        }
    return out


async def scored_runs(session: AsyncSession, run_ids: list[int], cfg: ScoreConfig) -> dict[str, Any]:
    settings = await settings_store.get_all(session)
    global_hints = settings.get("global_learned_hints") or None
    feeds = {f.id: f for f in (await session.execute(select(Feed))).scalars()}
    runs = list(
        (await session.execute(select(EvalRun).where(EvalRun.id.in_(run_ids)).order_by(EvalRun.id))).scalars()
    )
    label_rows = (
        await session.execute(
            select(EvalLabel, Episode)
            .join(Episode, EvalLabel.episode_id == Episode.id)
            .where(EvalLabel.verdict == "verified")
        )
    ).all()
    episodes: dict[str, dict[str, Any]] = {}
    hashes: dict[int, str | None] = {}
    for label, ep in label_rows:
        feed = feeds.get(ep.feed_id)
        hashes[ep.id] = label.inputs_hash
        episodes[str(ep.id)] = {
            "episode_id": ep.id,
            "episode_title": label.episode_title or ep.title,
            "feed_title": feed.title if feed else "",
            "duration_s": label.duration_s or 0.0,
            "promo": label.promo,
            "truth": [[t["start"], t["end"], t["category"]] for t in json.loads(label.truth_json or "[]")],
            "optional": labels.label_optional(label),
            "leak": labels.hints_leak(label, feed, global_hints),
        }

    results = (
        (await session.execute(select(EvalResult).where(EvalResult.run_id.in_([r.id for r in runs]))))
        .scalars()
        .all()
        if runs
        else []
    )
    by_run: dict[int, list[EvalResult]] = defaultdict(list)
    for r in results:
        by_run[r.run_id].append(r)

    # one CLI-shaped report per run, holding only results that still count
    reports: list[dict[str, Any]] = []
    stale: dict[int, int] = {}
    for run in runs:
        usable, stale_eps = [], set()
        for r in by_run.get(run.id, []):
            if r.episode_id not in hashes:
                continue  # the episode is no longer verified
            if r.inputs_hash != hashes[r.episode_id]:
                stale_eps.add(r.episode_id)
                continue
            usable.append(r)
        stale[run.id] = len(stale_eps)
        used = {str(r.episode_id) for r in usable}
        reports.append({
            "variant": {"name": run.name},
            "hints_mode": run.hints_mode,
            "episodes": {
                eid: {**ep, "hints_leak": run.hints_mode == "current" and ep["leak"]}
                for eid, ep in episodes.items()
                if eid in used
            },
            "runs": [
                {
                    "episode_id": r.episode_id,
                    "repeat": r.repeat,
                    "outcome": r.outcome,
                    "segments": json.loads(r.segments_json or "[]"),
                    "error": r.error,
                    "calls": r.calls,
                    "retries": r.retries,
                    "llm_elapsed_s": r.llm_elapsed_s,
                    "cost_usd": r.cost_usd,
                }
                for r in usable
            ],
        })

    common = summarize.common_episodes(reports) if reports else []
    leaked = summarize.leaked_episodes(reports, common) if common else set()
    clean = [e for e in common if e not in leaked]

    out_runs = []
    for run, report in zip(runs, reports, strict=True):
        per_episode: dict[str, Any] = {}
        for eid in common:
            ep = episodes[eid]
            repeats = []
            for r in sorted((x for x in report["runs"] if str(x["episode_id"]) == eid), key=lambda x: x["repeat"]):
                s = score_episode(
                    episode_id=ep["episode_id"],
                    duration_s=float(ep["duration_s"]),
                    truth=[(t[0], t[1]) for t in ep["truth"]],
                    optional=[(o[0], o[1]) for o in ep["optional"]],
                    truth_promo=bool(ep["promo"]),
                    predicted=[(seg[0], seg[1]) for seg in r["segments"]],
                    outcome=r["outcome"],
                    cfg=cfg,
                )
                repeats.append({
                    "repeat": r["repeat"],
                    "outcome": r["outcome"],
                    "outcome_ok": s.outcome_ok,
                    "segments": r["segments"],
                    "error": r["error"],
                    "scored": s.seconds_scored,
                    "content_cut_s": round(s.content_cut_s, 1),
                    "missed_ad_s": round(s.missed_ad_s, 1),
                    "weighted_s": round(s.weighted_error_s(cfg), 1),
                })
            scored = [x for x in repeats if x["scored"]]
            per_episode[eid] = {
                "weighted_s": statistics.fmean(x["weighted_s"] for x in scored) if scored else None,
                "content_cut_s": statistics.fmean(x["content_cut_s"] for x in scored) if scored else None,
                "missed_ad_s": statistics.fmean(x["missed_ad_s"] for x in scored) if scored else None,
                "repeats": repeats,
            }
        costs = [r["cost_usd"] for r in report["runs"] if str(r["episode_id"]) in set(common)]
        out_runs.append({
            "run_id": run.id,
            "model_id": run.model_id,
            "name": run.name,
            "model": run.model,
            "status": run.status,
            "hints_mode": run.hints_mode,
            "repeats": run.repeats,
            "created_at": run.created_at,
            "fingerprint": run.fingerprint,
            "stale_episodes": stale[run.id],
            "episodes_with_results": len(report["episodes"]),
            "metrics": summarize.summarize_report(report, common, cfg)["metrics"] if common else None,
            "metrics_clean": summarize.summarize_report(report, clean, cfg)["metrics"]
            if leaked and clean
            else None,
            "total_cost_usd": sum(costs) if costs and all(c is not None for c in costs) else None,
            "episodes": per_episode,
        })

    return {
        "config": {"content_weight": cfg.content_weight, "collar_s": cfg.collar_s, "blunder_s": cfg.blunder_s},
        "metric_rows": summarize.metric_rows(),
        "episodes": [{**episodes[eid], "hints_leak": eid in leaked} for eid in common],
        "verified_episodes": len(episodes),
        "leaked": len(leaked),
        # runs made under different hints or detection settings aren't a like-for-like comparison
        "mixed_conditions": len({r.fingerprint for r in runs if r.fingerprint}) > 1,
        "runs": out_runs,
    }
