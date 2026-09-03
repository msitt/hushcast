"""download step: a dead/stale enclosure URL is refreshed from the source
feed and retried once, rather than permanently stranding the episode."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Response

from hushcast import db
from hushcast.audio import ffmpeg
from hushcast.config import get_config
from hushcast.models import Episode, Feed
from hushcast.pipeline.context import EpisodeContext
from hushcast.pipeline.steps import download as download_step
from hushcast.settings_store import DEFAULTS

GOOD_BYTES = b"x" * (download_step.MIN_PLAUSIBLE_BYTES + 1000)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_stub_app(state: dict) -> FastAPI:
    """Serves a feed whose enclosure URL for guid "ep1" can rotate mid-test
    (state["current"] switches from "old" to "new"), plus the two audio
    endpoints themselves ("old" always 404s, as if the link had died)."""
    app = FastAPI()

    @app.get("/feed.xml")
    def feed():
        port = state["port"]
        url = f"http://127.0.0.1:{port}/{state['current']}.mp3"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title><link>http://example.com</link><description>d</description>
<item><title>Ep</title><guid isPermaLink="false">ep1</guid>
<enclosure url="{url}" length="0" type="audio/mpeg"/></item>
</channel></rss>"""
        return Response(content=xml, media_type="application/rss+xml")

    @app.get("/old.mp3")
    def old():
        return Response(status_code=404)

    @app.get("/new.mp3")
    def new():
        return Response(content=GOOD_BYTES, media_type="audio/mpeg")

    return app


@pytest.fixture
def stub_server():
    state = {"current": "old"}
    port = _free_port()
    state["port"] = port
    app = _make_stub_app(state)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("stub server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", state
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
async def app_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HUSHCAST_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HUSHCAST_DATA_DIR", str(tmp_path / "data"))
    get_config.cache_clear()
    config = get_config()
    config.ensure_dirs()
    await db.init_db()
    yield config
    await db.dispose_db()


async def test_dead_url_refreshes_from_feed_and_retries(stub_server, app_db, monkeypatch):
    base_url, state = stub_server

    async def fake_probe(path):
        return 12.3

    monkeypatch.setattr(ffmpeg, "probe_duration", fake_probe)

    async with db.session_factory()() as session:
        feed = Feed(slug="s", source_url=f"{base_url}/feed.xml")
        session.add(feed)
        await session.flush()
        episode = Episode(
            feed_id=feed.id, guid="ep1", title="Ep",
            source_enclosure_url=f"{base_url}/old.mp3",  # stale: the source has since rotated
        )
        session.add(episode)
        await session.commit()
        feed_id, episode_id = feed.id, episode.id

    # the feed now serves a different (working) URL for the same guid
    state["current"] = "new"

    ctx = EpisodeContext(
        episode_id=episode_id, feed_id=feed_id, episode_title="Ep", podcast_title="pod",
        guid="ep1", source_enclosure_url=f"{base_url}/old.mp3", whitelisted=False,
        detection_hints=None, learned_hints=None, config=app_db, settings=dict(DEFAULTS),
    )

    await download_step.run(ctx)

    assert ctx.original_path is not None and ctx.original_path.exists()
    assert ctx.original_path.read_bytes() == GOOD_BYTES
    assert any("refreshed from feed" in line for line in ctx.log)

    async with db.session_factory()() as session:
        refreshed = await session.get(Episode, episode_id)
        assert refreshed.source_enclosure_url == f"{base_url}/new.mp3"


async def test_episode_gone_from_feed_raises_original_error(stub_server, app_db, monkeypatch):
    base_url, state = stub_server

    async with db.session_factory()() as session:
        feed = Feed(slug="s", source_url=f"{base_url}/feed.xml")
        session.add(feed)
        await session.flush()
        episode = Episode(
            feed_id=feed.id, guid="no-such-guid", title="Ep",
            source_enclosure_url=f"{base_url}/old.mp3",
        )
        session.add(episode)
        await session.commit()
        feed_id, episode_id = feed.id, episode.id

    ctx = EpisodeContext(
        episode_id=episode_id, feed_id=feed_id, episode_title="Ep", podcast_title="pod",
        guid="no-such-guid", source_enclosure_url=f"{base_url}/old.mp3", whitelisted=False,
        detection_hints=None, learned_hints=None, config=app_db, settings=dict(DEFAULTS),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await download_step.run(ctx)
    assert any("no longer listed in the source feed" in line for line in ctx.log)
