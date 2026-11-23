"""Score eval reports and lay them side by side.

Scoring happens here, not in the runner, so the weighting and collar can be
changed after the fact without re-running anything.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from .scoring import Aggregate, EpisodeScore, ScoreConfig, aggregate, score_episode


def common_episodes(reports: list[dict[str, Any]]) -> list[str]:
    sets = [set(r["episodes"]) for r in reports]
    return sorted(set.intersection(*sets), key=int) if sets else []


def disambiguate_names(reports: list[dict[str, Any]]) -> None:
    """Give reports that share a variant name distinct column labels, in place."""
    names = [r["variant"]["name"] for r in reports]
    for r in reports:
        if names.count(r["variant"]["name"]) > 1:
            r["variant"]["name"] += f" @{r['created_at'][5:16].replace('T', ' ')}"


def leaked_episodes(reports: list[dict[str, Any]], episode_ids: list[str]) -> set[str]:
    """Episodes whose corrections may have been distilled into the hints any of the runs used."""
    return {e for e in episode_ids if any(r["episodes"][e].get("hints_leak") for r in reports)}


def episode_scores(report: dict[str, Any], episode_ids: list[str], cfg: ScoreConfig) -> dict[int, list[EpisodeScore]]:
    """Scores per repeat index, restricted to episode_ids."""
    wanted = set(episode_ids)
    by_repeat: dict[int, list[EpisodeScore]] = defaultdict(list)
    for run in report["runs"]:
        key = str(run["episode_id"])
        if key not in wanted:
            continue
        ep = report["episodes"][key]
        by_repeat[run["repeat"]].append(
            score_episode(
                episode_id=run["episode_id"],
                duration_s=float(ep["duration_s"]),
                truth=[(t[0], t[1]) for t in ep["truth"]],
                truth_promo=bool(ep["promo"]),
                predicted=[(s[0], s[1]) for s in run["segments"]],
                outcome=run["outcome"],
                cfg=cfg,
            )
        )
    return dict(by_repeat)


def _spread(values: list[float | None]) -> dict[str, float | None]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return {"mean": None, "min": None, "max": None}
    return {"mean": statistics.fmean(vals), "min": min(vals), "max": max(vals)}


def summarize_report(report: dict[str, Any], episode_ids: list[str], cfg: ScoreConfig) -> dict[str, Any]:
    by_repeat = episode_scores(report, episode_ids, cfg)
    aggs: list[Aggregate] = [aggregate(scores, cfg) for _, scores in sorted(by_repeat.items())]
    metrics = {name: _spread([getattr(a, name) for a in aggs]) for name in Aggregate.__dataclass_fields__}

    wanted = set(episode_ids)
    runs = [r for r in report["runs"] if str(r["episode_id"]) in wanted]
    # A cost is only shown when every detection has one. Zero is a real cost
    # (a free model), a missing one is not.
    costs = [r.get("cost_usd") for r in runs]
    cost_known = bool(costs) and all(c is not None for c in costs)
    calls = sum(r["calls"] for r in runs)
    metrics["cost_per_episode_usd"] = _spread([statistics.fmean(costs)] if cost_known else [])
    metrics["llm_s_per_episode"] = _spread([statistics.fmean([r["llm_elapsed_s"] for r in runs])] if runs else [])
    metrics["malformed_retry_pct"] = _spread([100 * sum(r["retries"] for r in runs) / calls] if calls else [])
    return {
        "name": report["variant"]["name"],
        "model": report.get("effective_settings", {}).get("llm_model"),
        "repeats": len(aggs),
        "metrics": metrics,
        "repeat_aggregates": [asdict(a) for a in aggs],
    }


# (key, label, format). Lower is better unless the label says otherwise.
ROWS = [
    ("weighted_error_per_h", "weighted error, s/h", "{:.1f}"),
    ("content_cut_per_h", "content cut, s/h", "{:.1f}"),
    ("missed_ad_per_h", "ad missed, s/h", "{:.1f}"),
    ("blunders", "blunder episodes", "{:.1f}"),
    ("ad_recall", "ad recall (higher better)", "pct"),
    ("content_cut_pct", "content cut, % of content", "{:.2f}"),
    ("block_recall", "ad blocks found (higher better)", "pct"),
    ("spurious_blocks", "spurious blocks", "{:.1f}"),
    ("median_abs_start_err_s", "median |start error|, s", "{:.1f}"),
    ("median_abs_end_err_s", "median |end error|, s", "{:.1f}"),
    ("outcome_accuracy", "outcome correct (higher better)", "pct"),
    ("reviews", "circuit-breaker reviews", "{:.1f}"),
    ("errors", "failed episodes", "{:.1f}"),
    ("malformed_retry_pct", "malformed-output retries, % of calls", "{:.1f}"),
    ("cost_per_episode_usd", "cost per episode, USD", "{:.4f}"),
    ("llm_s_per_episode", "LLM time per episode, s", "{:.1f}"),
]


def metric_rows() -> list[dict[str, Any]]:
    """ROWS in a form a UI can render: label, number format, and which way is better."""
    out = []
    for key, label, fmt in ROWS:
        higher = label.endswith(" (higher better)")
        out.append({
            "key": key,
            "label": label.removesuffix(" (higher better)"),
            "kind": "pct" if fmt == "pct" else "num",
            "digits": 1 if fmt == "pct" else int(fmt[3]),
            "better": "higher" if higher else "lower",
        })
    return out


def _fmt(value: float | None, fmt: str) -> str:
    if value is None:
        return "-"
    if fmt == "pct":
        return f"{100 * value:.1f}%"
    return fmt.format(value)


def _cell(spread: dict[str, float | None], fmt: str) -> str:
    if spread["mean"] is None:
        return "-"
    out = _fmt(spread["mean"], fmt)
    if spread["min"] != spread["max"]:
        out += f" ({_fmt(spread['min'], fmt)}–{_fmt(spread['max'], fmt)})"
    return out


def render_table(summaries: list[dict[str, Any]]) -> str:
    header = ["metric"] + [s["name"] for s in summaries]
    rows = [header]
    for key, label, fmt in ROWS:
        rows.append([label] + [_cell(s["metrics"][key], fmt) for s in summaries])
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for n, r in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def render_per_episode(reports: list[dict[str, Any]], episode_ids: list[str], cfg: ScoreConfig) -> str:
    """Per episode and report: mean weighted error, then content cut / ad missed, in seconds."""
    per_report = [episode_scores(r, episode_ids, cfg) for r in reports]
    header = ["episode", "len"] + [r["variant"]["name"] for r in reports]
    rows = [header]
    for eid in episode_ids:
        ep = reports[0]["episodes"][eid]
        title = (ep.get("episode_title") or "")[:40]
        cells = [f"{eid} {title}", f"{float(ep['duration_s']) / 60:.0f}m"]
        for by_repeat in per_report:
            scores = [s for reps in by_repeat.values() for s in reps if s.episode_id == int(eid)]
            if not scores:
                cells.append("-")
            elif not scores[0].seconds_scored:
                ok = sum(1 for s in scores if s.outcome_ok)
                cells.append(f"promo {ok}/{len(scores)} correct")
            else:
                w = statistics.fmean(s.weighted_error_s(cfg) for s in scores)
                c = statistics.fmean(s.content_cut_s for s in scores)
                m = statistics.fmean(s.missed_ad_s for s in scores)
                flags = {s.outcome for s in scores} - {"cut"}
                cells.append(f"{w:.0f} ({c:.0f}/{m:.0f})" + (f" {','.join(sorted(flags))}" if flags else ""))
        rows.append(cells)
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for n, r in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)
