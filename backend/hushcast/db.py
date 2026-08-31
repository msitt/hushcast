"""Async SQLAlchemy engine/session for SQLite (WAL mode, busy timeout)."""
import asyncio
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_config


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_config().database_url, echo=False)

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def _run_migrations() -> None:
    """Bring the schema to head via Alembic (sync, call from a thread)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))

    # Databases created by the pre-Alembic create_all() path have the full
    # schema but no alembic_version table. Stamp them instead of re-creating.
    sync_url = get_config().database_url.replace("+aiosqlite", "")
    engine = create_engine(sync_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()
    if "feeds" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")


async def init_db() -> None:
    from . import models  # noqa: F401  (register mappings)

    await asyncio.to_thread(_run_migrations)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
