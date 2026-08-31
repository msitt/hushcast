# AGENTS.md

This file provides guidance to AI coding agents working with code in this repository.

## What this is

Self-hosted podcast ad-removal server. It polls source RSS feeds, runs new episodes through a pipeline (download → transcribe → LLM ad detection → ffmpeg cut), and serves clean, token-gated per-podcast RSS feeds (`/p/{token}/{slug}/feed.xml`, `/p/{token}/audio/{id}.mp3`). Backend: Python 3.14 / FastAPI / SQLite (SQLAlchemy async + Alembic), managed with uv. Frontend: React 19 / Vite / TypeScript in `frontend/`. Ships as a single Docker container.

## Commands

```bash
uv sync                                              # install backend deps
uv run pytest                                        # all backend tests
uv run pytest tests/test_segments.py                 # one file
uv run pytest tests/test_segments.py -k merge        # one test by keyword
uv run ruff check backend tests                      # backend lint (CI enforces this)
uv run uvicorn hushcast.main:app --reload --port 4874  # backend dev server

cd frontend
npm install
npm run dev      # Vite dev server, proxies /api and /p to :4874
npm run build    # tsc --noEmit + vite build
npm run lint     # oxlint, warnings fail (CI enforces this)
```

ffmpeg must be on PATH. Config comes from `HUSHCAST_*` env vars or a `.env` file (see `.env.example`). Everything else is runtime settings in SQLite, edited via the UI.

### Migrations

The app auto-migrates to head on startup. After changing `models.py`:

```bash
uv run alembic revision --autogenerate -m "describe the change"   # from repo root
uv run alembic upgrade head    # or just restart the app
```

Autogenerate diffs models against whatever DB the env vars point at, so run it against an up-to-date dev DB and review the generated file. `render_as_batch=True` is on because SQLite can't ALTER most things.

### Releases

Releases are cut from the GitHub UI: Actions → Release → "Run workflow" on master with the new version. The workflow (`.github/workflows/release.yml` + `.github/scripts/prepare_release.py`) runs tests, bumps versions, rolls the changelog, builds and pushes the multi-arch image to `ghcr.io/msitt/hushcast`, pushes the release commit + tag, and creates a GitHub Release from the changelog section. Version's single source of truth is `__version__` in `backend/hushcast/__init__.py` (pyproject reads it via hatchling). The workflow requires passing tests and a non-empty `[Unreleased]` section in CHANGELOG.md.

**Update CHANGELOG.md as part of any significant user-visible change**: new features, behavior changes, and important bugfixes get an entry under `[Unreleased]` in the same commit. Minor changes don't need one even if user-visible (e.g. styling tweaks, copy/wording adjustments), and neither do internal refactors/test-only changes. The release script aborts if `[Unreleased]` is empty at release time, so write the entry when the change lands, not at release. `docker-compose.dev.yml` builds from the checkout, and `docker-compose.yml` pulls the released image.

## Architecture

### Pipeline (`backend/hushcast/pipeline/`)

The core of the app. Episode statuses are plain strings with an explicit transition table in `pipeline/state.py`:

```
discovered → queued → downloading → transcribing → detecting → cutting → processed
```

plus `skipped` (pre-existing episodes are never auto-processed by design, queue them manually via Process), `failed` (retryable, → queued), and `expired` (retention cleanup). Whitelisted feeds run a shortened copy-through pipeline (download → cut → finalize, no transcribe/detect). All status changes must go through `validate_transition`.

- `scheduler.py` runs APScheduler jobs: feed polling, retention.
- `worker.py`: single in-process asyncio queue. Defines the step lists (`FULL_PIPELINE` / `WHITELISTED_PIPELINE`) as `(job step name, episode status, step fn)` tuples. Two steps can share a status (cues reuses `transcribing`, finalize reuses `cutting`). `_run_step` skips the transition when the status is unchanged. Steps are idempotent and resumable: on restart, episodes in `RESUMABLE` states are re-queued.
- `steps/`: download, transcribe, cues, detect, cut, finalize. Each takes an `EpisodeContext` (`context.py`) carrying paths and settings.

### Detection (`backend/hushcast/detection/`)

- `llm.py`: OpenAI-compatible chat-completions client. Single-pass over the whole transcript by default. Chunks only when over `llm_context_budget_tokens`. Malformed/truncated chunk output is retried.
- `prompts.py`: prompt assembly. Per-feed `detection_hints` and correction-derived `learned_hints` are appended.
- `segments.py`: pure, heavily unit-tested post-processing of raw LLM segments, in a fixed order: clamp → snap to transcript boundaries → threshold filters → merge → cue bridging/edge extension → word-boundary refinement → circuit breaker (`DetectionRejected` when too much would be cut).
- `refine.py`: word-level boundary refinement (widest inter-word silence). Always on, no-op without word timestamps.
- `corrections.py`: distills user corrections into learned hints (training signal only, review-before-apply).

### Cues (`backend/hushcast/cues/`)

Optional audio-cue segmentation (music/silence) feeding detection, with a pluggable provider: built-in ffmpeg `silencedetect` (default) or a remote inaSpeechSegmenter-style service. Cue failures never fail the pipeline (remote → silencedetect → empty).

### Serving (`backend/hushcast/serve/`, `rss/`)

`rss/` fetches source feeds and rewrites served ones. `serve/` exposes the token-gated public routes with byte-range audio support. Only `processed` episodes appear in served feeds. Original episode GUIDs are preserved. `/p/*` is deliberately unauthenticated (podcast clients can't send headers), so the path token is the auth. `/api/*` is protected by the built-in login (`auth.py`: credentials in the settings table under keys outside `DEFAULTS`, scrypt-hashed. Stateless HMAC-signed session cookie whose key mixes `config_dir/session_secret` with the password hash, so a password change invalidates all sessions). Fresh installs boot into "setup" mode until the first visit creates the login. `HUSHCAST_AUTH=disabled` turns auth off for reverse-proxy-auth deployments. The SPA shell and `/assets` stay public (the login page needs them).

### Other conventions

- Two storage roots with different value: `HUSHCAST_CONFIG_DIR` (SQLite + settings, small, backed up) vs `HUSHCAST_DATA_DIR` (audio/transcripts/scratch, bulky, replaceable). Don't put anything precious under data.
- All datetimes are UTC. `UTCDateTime` in `models.py` re-attaches tzinfo on load from SQLite.
- Tests (`tests/`) are unit tests around the pure/parsing parts (segments, chunking, RSS parse/rewrite, state machine, ffmpeg cut, range serving) with fixtures in `tests/fixtures/`. pytest-asyncio is in auto mode.

## Coding style

- Don't use unnecessary semicolons in sentences. Split into two sentences with a period, or use another connector (comma, colon, "and"/"but") as fits. This applies to prose everywhere in the app: docs, UI copy, comments, docstrings, log/error messages. Code syntax (statement separators, TS type members, literal values like the User-Agent header) is unaffected.
- Never use em-dashes.
