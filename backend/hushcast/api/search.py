"""Podcast directory search for the add-podcast dialog."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import search as directory
from ..models import Feed
from .deps import get_session

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchResultOut(directory.SearchResult):
    already_added: bool = False


@router.get("")
async def search_podcasts(
    q: str = Query(min_length=2, max_length=200),
    session: AsyncSession = Depends(get_session),
) -> list[SearchResultOut]:
    try:
        results = await directory.search(q)
    except directory.SearchError as exc:
        raise HTTPException(502, str(exc)) from exc

    existing = set((await session.execute(select(Feed.source_url))).scalars().all())
    return [
        SearchResultOut(**r.model_dump(), already_added=r.feed_url in existing)
        for r in results
    ]
