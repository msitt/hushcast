"""Eval command line.

    python -m hushcast.evals fetch http://hushcast:4874 -o .evals/dataset.json
    python -m hushcast.evals run --dataset .evals/dataset.json --variant variants/glm.toml
    python -m hushcast.evals summarize

See docs/evals.md.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import runner, summarize
from .scoring import ScoreConfig

DEFAULT_DIR = Path(".evals")


def _cmd_fetch(args: argparse.Namespace) -> int:
    dataset = runner.load_dataset(args.url, cookie=args.cookie)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataset), encoding="utf-8")
    print(f"wrote {len(dataset['episodes'])} verified episode(s) to {out} ({dataset.get('excluded', 0)} excluded)")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    dataset = runner.load_dataset(args.dataset, cookie=args.cookie)
    if not dataset.get("episodes"):
        print("the dataset has no verified episodes", file=sys.stderr)
        return 1
    variant = runner.Variant.load(Path(args.variant))
    ids = {int(x) for x in args.episodes.split(",")} if args.episodes else None
    report = asyncio.run(
        runner.run_variant(
            dataset,
            variant,
            repeats=args.repeats,
            concurrency=args.concurrency,
            hints_mode=args.hints,
            cache_dir=Path(args.cache_dir),
            episode_ids=ids,
            dataset_source=args.dataset,
        )
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.output) if args.output else DEFAULT_DIR / "reports" / f"{variant.name}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    cfg = ScoreConfig()
    print(summarize.render_table([summarize.summarize_report(report, list(report["episodes"]), cfg)]))
    print()
    print(f"wrote {out}")
    print("compare the newest report of every variant with: python -m hushcast.evals summarize")
    return 0


def _report_paths(args: argparse.Namespace) -> list[Path]:
    """The reports to compare. Globs are expanded here because PowerShell and
    cmd pass them through unexpanded. With none given: the newest report of
    each variant under .evals/reports."""
    if args.reports:
        paths = [Path(m) for pattern in args.reports for m in (sorted(glob.glob(pattern)) or [pattern])]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(f"no such report: {', '.join(missing)}")
        return paths
    newest: dict[str, tuple[str, Path]] = {}
    for path in sorted((DEFAULT_DIR / "reports").glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            name, created = report["variant"]["name"], report["created_at"]
        except (ValueError, KeyError, TypeError):
            continue
        if name not in newest or created > newest[name][0]:
            newest[name] = (created, path)
    return [path for _, path in sorted(newest.values())]


def _cmd_summarize(args: argparse.Namespace) -> int:
    paths = _report_paths(args)
    if not paths:
        print(f"no reports found in {DEFAULT_DIR / 'reports'}, run a variant first", file=sys.stderr)
        return 1
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    summarize.disambiguate_names(reports)
    cfg = ScoreConfig(collar_s=args.collar, content_weight=args.content_weight, blunder_s=args.blunder)
    episode_ids = summarize.common_episodes(reports)
    if not episode_ids:
        print("the reports share no episodes", file=sys.stderr)
        return 1
    skipped = sum(len(r["episodes"]) for r in reports) - len(episode_ids) * len(reports)
    summaries = [summarize.summarize_report(r, episode_ids, cfg) for r in reports]
    if args.json:
        print(json.dumps({"config": cfg.__dict__, "episodes": episode_ids, "summaries": summaries}, indent=1))
        return 0
    for path, r in zip(paths, reports, strict=True):
        print(
            f"{r['variant']['name']}: {r.get('effective_settings', {}).get('llm_model', '?')}, "
            f"hints {r.get('hints_mode', '?')}, {r.get('repeats', '?')} repeat(s), {r['created_at'][:16]}  [{path}]"
        )
    print()
    print(
        f"{len(episode_ids)} episode(s) common to all reports"
        + (f", {skipped} report-episode(s) not shared were left out" if skipped else "")
        + f". content weight {cfg.content_weight:g}, collar {cfg.collar_s:g}s, blunder over {cfg.blunder_s:g}s cut."
    )
    print()
    print(summarize.render_table(summaries))
    leaked = summarize.leaked_episodes(reports, episode_ids)
    clean = [e for e in episode_ids if e not in leaked]
    if leaked and clean:
        print()
        print(
            f"Without the {len(leaked)} episode(s) whose corrections fed the current hints "
            f"({len(clean)} left). If the ranking holds here, the leak doesn't matter:"
        )
        print(summarize.render_table([summarize.summarize_report(r, clean, cfg) for r in reports]))
    elif leaked:
        print()
        print("Every episode's corrections fed the current hints, so these scores may be flattered.")
    if args.per_episode:
        print()
        print("weighted error per episode, s (content cut / ad missed)")
        print(summarize.render_per_episode(reports, episode_ids, cfg))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hushcast.evals", description="hushcast detection evals")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download the eval dataset from a hushcast instance (read-only GET)")
    p.add_argument("url", help="hushcast base URL, e.g. http://localhost:4874")
    p.add_argument("-o", "--output", default=str(DEFAULT_DIR / "dataset.json"))
    p.add_argument("--cookie", help="Cookie header for instances with login enabled, e.g. hushcast_session=...")
    p.set_defaults(fn=_cmd_fetch)

    p = sub.add_parser("run", help="run one variant over the dataset and write a report")
    p.add_argument("--dataset", default=str(DEFAULT_DIR / "dataset.json"), help="dataset file or hushcast base URL")
    p.add_argument("--variant", required=True, help="variant TOML file")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--hints", choices=["current", "none"], default="current",
                   help="current: the hints production uses today (default). none: no hints at all")
    p.add_argument("--episodes", help="comma-separated episode ids to limit the run to")
    p.add_argument("--cache-dir", default=str(DEFAULT_DIR / "cache"))
    p.add_argument("-o", "--output", help="report path (default .evals/reports/<variant>-<time>.json)")
    p.add_argument("--cookie", help="Cookie header when --dataset is a URL with login enabled")
    p.set_defaults(fn=_cmd_run)

    p = sub.add_parser("summarize", help="score reports and compare them side by side")
    p.add_argument("reports", nargs="*",
                   help="report files or globs (default: the newest report of each variant in .evals/reports)")
    p.add_argument("--content-weight", type=float, default=3.0,
                   help="how many seconds of missed ad one second of cut content costs (default 3)")
    p.add_argument("--collar", type=float, default=1.0, help="seconds ignored around each truth boundary")
    p.add_argument("--blunder", type=float, default=5.0, help="content cut per episode that counts as a blunder")
    p.add_argument("--per-episode", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_summarize)

    args = parser.parse_args(argv)
    # episode titles are full of curly quotes that legacy Windows code pages can't print
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return args.fn(args)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
