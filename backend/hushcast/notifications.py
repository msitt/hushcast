"""Push notifications for events that need a human's attention.

Delivery is via Apprise (https://github.com/caronc/apprise): users configure
one or more Apprise URLs (Discord, Slack, ntfy, email, Telegram, a generic
webhook, and ~100 other services), we just hand it a title/body. Dispatch is
fire-and-forget: a slow or broken notification target must never delay the
pipeline, so callers get a no-op on misconfiguration and errors are only
logged, never raised.
"""
from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Any

import apprise

log = logging.getLogger(__name__)

# Must use jsDelivr for hotlinking from the repo
_ICON_URL = "https://cdn.jsdelivr.net/gh/msitt/hushcast@master/frontend/public/icon-512.png"
_ASSET = apprise.AppriseAsset(
    app_id="Hushcast",
    app_desc="Self-hosted podcast ad-removal server",
    app_url="https://github.com/msitt/hushcast",
    image_url_mask=_ICON_URL,
    image_url_logo=_ICON_URL,
)


class NotificationEvent(StrEnum):
    EPISODE_RETRIES_EXHAUSTED = "episode_retries_exhausted"
    FEED_POLL_FAILING = "feed_poll_failing"


EVENT_LABELS = {
    NotificationEvent.EPISODE_RETRIES_EXHAUSTED: "Episode retries exhausted",
    NotificationEvent.FEED_POLL_FAILING: "Feed polling failing repeatedly",
}


def notify(event: NotificationEvent, title: str, body: str, settings: dict[str, Any]) -> None:
    """Fire a notification to the configured channels, if enabled. Never blocks."""
    urls = settings.get("notification_urls") or []
    events = settings.get("notification_events") or {}
    if not urls or not events.get(event.value, True):
        return
    asyncio.create_task(_send(urls, title, body))


async def _send(urls: list[str], title: str, body: str) -> None:
    try:
        apobj = _build(urls)
        if apobj:
            await apobj.async_notify(title=title, body=body)
    except Exception:
        log.exception("notification dispatch failed")


def _build(urls: list[str]) -> apprise.Apprise:
    apobj = apprise.Apprise(asset=_ASSET)
    for url in urls:
        if not apobj.add(url):
            log.warning("could not parse notification URL (check the scheme/format)")
    return apobj


async def send_test(urls: list[str]) -> tuple[bool, str]:
    """Used by the settings "Send test notification" action."""
    if not urls:
        return False, "no notification URLs configured"
    apobj = apprise.Apprise(asset=_ASSET)
    bad = sum(1 for u in urls if not apobj.add(u))
    if bad:
        return False, f"could not parse {bad} of {len(urls)} URL(s), check the scheme/format"
    try:
        ok = await apobj.async_notify(
            title="Hushcast test notification",
            body="This is a test notification from Hushcast.",
        )
        return ok, ("sent" if ok else "one or more targets rejected the notification")
    except Exception as exc:
        return False, str(exc)
