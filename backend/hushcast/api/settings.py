"""Settings API: read (masked), update, provider connection tests."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .. import loglevel, settings_store
from ..detection.llm import LLMClient
from ..pipeline import scheduler
from ..transcription.openai_compat import build_transcriber
from .deps import get_session

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await settings_store.ensure_feed_token(session)
    return settings_store.masked(await settings_store.get_all(session))


@router.put("")
async def put_settings(
    body: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    if "log_level" in body:
        body["log_level"] = str(body["log_level"]).upper()
        if body["log_level"] not in loglevel.LEVELS:
            raise HTTPException(400, f"log_level must be one of {', '.join(loglevel.LEVELS)}")
    try:
        await settings_store.set_many(session, body)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    settings = await settings_store.get_all(session)
    if "poll_interval_minutes" in body:
        scheduler.reschedule_poll(int(settings["poll_interval_minutes"]))
    if "log_level" in body:
        loglevel.apply(loglevel.effective(settings))
    return settings_store.masked(settings)


@router.post("/regenerate-token")
async def regenerate_token(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    await settings_store.set_many(session, {"feed_token": ""})
    token = await settings_store.ensure_feed_token(session)
    return {"feed_token": token}


async def _effective_settings(
    session: AsyncSession, overrides: dict[str, Any] | None
) -> dict[str, Any]:
    """Stored settings overlaid with unsaved form values from the request.

    Masked secrets in the overrides mean "use the stored value".
    """
    settings = await settings_store.get_all(session)
    for key, value in (overrides or {}).items():
        if key not in settings_store.DEFAULTS:
            raise HTTPException(400, f"unknown setting: {key}")
        if key in settings_store.SECRET_KEYS and value == settings_store.MASK:
            continue
        settings[key] = value
    return settings


@router.post("/test/transcription")
async def test_transcription(
    overrides: dict[str, Any] | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await _effective_settings(session, overrides)
    try:
        await build_transcriber(settings).health_check()
        return {"ok": True, "message": "transcription server reachable"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/test/llm")
async def test_llm(
    overrides: dict[str, Any] | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    settings = await _effective_settings(session, overrides)
    try:
        await LLMClient(settings).health_check()
        return {"ok": True, "message": "LLM responded with valid JSON"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
