"""FastAPI application: API + public feed routes + SPA static files."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, logbuffer, loglevel, settings_store
from .api import episodes, feeds, settings as settings_api, system
from .config import get_config
from .db import dispose_db, init_db, session_factory
from .pipeline import scheduler
from .pipeline.worker import worker
from .serve import rss as public_routes

log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    # The stored level isn't readable until the DB is up, start from the env
    # override (or default) so early logs flow, then apply the effective level.
    logging.basicConfig(level=(config.log_level or settings_store.DEFAULTS["log_level"]).upper())
    logging.getLogger().addHandler(logbuffer.handler)
    config.ensure_dirs()
    await init_db()
    async with session_factory()() as session:
        await settings_store.ensure_feed_token(session)
        await auth.refresh(session)
        settings = await settings_store.get_all(session)
    loglevel.apply(loglevel.effective(settings))
    await worker.start()
    await scheduler.start()
    log.info(
        "hushcast %s ready (public_url=%s, auth=%s)",
        __version__,
        config.public_url,
        auth.mode(),
    )
    yield
    scheduler.stop()
    await worker.stop()
    await dispose_db()


app = FastAPI(title="hushcast", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """401 unauthenticated /api requests. /p/* (token-gated feeds), /healthz,
    and the SPA shell stay public. The login/setup endpoints exempt themselves
    via auth.PUBLIC_API_PATHS. No-op when HUSHCAST_AUTH=disabled."""
    if auth.is_protected(request.url.path) and not auth.request_authenticated(request):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(feeds.router)
app.include_router(episodes.router)
app.include_router(settings_api.router)
app.include_router(system.router)
app.include_router(public_routes.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "version": __version__}


class _ImmutableStaticFiles(StaticFiles):
    """Vite content-hashes every filename under /assets, so a stale copy can
    never be served under a current name: cache forever, skip revalidation."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if FRONTEND_DIST.exists():
    app.mount("/assets", _ImmutableStaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    # index.html references the hashed asset names, so it must be revalidated
    # on every visit (cheap 304 via ETag) or browsers heuristically cache it
    # and keep loading the previous deploy's bundle. Same for the other
    # un-hashed root files (favicon etc.).
    _NO_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # Unknown API/feed paths must 404, not silently return the SPA's HTML.
        if full_path.startswith(("api/", "p/")) or full_path in ("api", "p"):
            raise HTTPException(404, "not found")
        candidate = (FRONTEND_DIST / full_path).resolve()
        if (
            full_path
            and candidate.is_relative_to(FRONTEND_DIST.resolve())
            and candidate.is_file()
        ):
            return FileResponse(candidate, headers=_NO_CACHE)
        return FileResponse(FRONTEND_DIST / "index.html", headers=_NO_CACHE)
