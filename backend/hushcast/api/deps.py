from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session
