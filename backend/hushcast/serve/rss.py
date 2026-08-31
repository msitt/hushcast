"""Public (token-gated) feed + audio routes consumed by podcast clients."""
from __future__ import annotations

import secrets as pysecrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_session
from ..config import get_config
from ..models import Episode, Feed
from ..pipeline import state
from ..rss.rewrite import build_feed_xml
from .. import settings_store

router = APIRouter(prefix="/p", tags=["public"])


async def _check_token(session: AsyncSession, token: str) -> None:
    expected = await settings_store.ensure_feed_token(session)
    if not pysecrets.compare_digest(token, expected):
        raise HTTPException(404)  # 404 (not 403) to avoid confirming the URL scheme


@router.get("/{token}/{slug}/feed.xml")
async def serve_feed(
    token: str, slug: str, session: AsyncSession = Depends(get_session)
) -> Response:
    await _check_token(session, token)
    feed = (
        await session.execute(select(Feed).where(Feed.slug == slug))
    ).scalar_one_or_none()
    if feed is None:
        raise HTTPException(404, "feed not found")
    episodes = (
        await session.execute(
            select(Episode)
            .where(Episode.feed_id == feed.id, Episode.status == state.PROCESSED)
            .order_by(Episode.published_at.desc().nulls_last())
        )
    ).scalars().all()
    xml = build_feed_xml(
        feed, episodes, public_url=get_config().public_url, token=token
    )
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/{token}/audio/{episode_id}.mp3")
@router.head("/{token}/audio/{episode_id}.mp3")
async def serve_audio(
    token: str, episode_id: int, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    await _check_token(session, token)
    episode = await session.get(Episode, episode_id)
    if episode is None or episode.status != state.PROCESSED or not episode.processed_path:
        raise HTTPException(404, "episode not available")
    path = Path(episode.processed_path)
    if not path.exists():
        raise HTTPException(404, "audio file missing")
    # FileResponse (Starlette) handles Range/206 and HEAD for us.
    return FileResponse(path, media_type="audio/mpeg", filename=f"{episode_id}.mp3")
