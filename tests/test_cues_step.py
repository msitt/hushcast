"""Cues pipeline step: idempotency, off-mode, and failure fallbacks."""
import json
from pathlib import Path

from hushcast.config import AppConfig
from hushcast.cues.base import Cue
from hushcast.pipeline.context import EpisodeContext
from hushcast.pipeline.steps import cues as cues_step
from hushcast.settings_store import DEFAULTS


def make_ctx(tmp_path: Path, **settings_overrides) -> EpisodeContext:
    config = AppConfig(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    config.ensure_dirs()
    settings = dict(DEFAULTS)
    settings.update(settings_overrides)
    ctx = EpisodeContext(
        episode_id=1, feed_id=1, episode_title="ep", podcast_title="pod", guid="g",
        source_enclosure_url="http://x/audio.mp3", whitelisted=False,
        detection_hints=None, learned_hints=None, config=config, settings=settings,
    )
    ctx.original_path = tmp_path / "data" / "audio" / "original" / "1.mp3"
    ctx.original_path.write_bytes(b"fake audio")
    return ctx


async def test_off_writes_nothing(tmp_path):
    ctx = make_ctx(tmp_path, cue_provider="off")
    await cues_step.run(ctx)
    assert not ctx.cues_path.exists()
    assert cues_step.load_cues(ctx) is None


async def test_skips_when_file_exists(tmp_path):
    ctx = make_ctx(tmp_path, cue_provider="silence")
    ctx.cues_path.write_text(json.dumps([{"start": 1.0, "end": 2.0, "kind": "silence"}]))
    await cues_step.run(ctx)
    assert any("already on disk" in line for line in ctx.log)
    assert cues_step.load_cues(ctx) == [Cue(start=1.0, end=2.0, kind="silence")]


async def test_silencedetect_failure_writes_empty(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, cue_provider="silence")

    async def boom(self, path):
        raise RuntimeError("no ffmpeg here")

    monkeypatch.setattr("hushcast.cues.silencedetect.SilenceDetectProvider.detect", boom)
    await cues_step.run(ctx)  # must not raise
    assert ctx.cues_path.exists()
    assert cues_step.load_cues(ctx) == []
    assert ctx.metrics["cue_provider_used"] == "none"


async def test_remote_failure_falls_back_to_silencedetect(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, cue_provider="remote", cue_remote_base_url="http://localhost:1")

    async def remote_boom(self, path):
        raise RuntimeError("connection refused")

    async def fake_silence(self, path):
        return [Cue(start=0.0, end=3.0, kind="silence")]

    monkeypatch.setattr("hushcast.cues.ina_client.InaClient.detect", remote_boom)
    monkeypatch.setattr("hushcast.cues.silencedetect.SilenceDetectProvider.detect", fake_silence)
    await cues_step.run(ctx)
    assert cues_step.load_cues(ctx) == [Cue(start=0.0, end=3.0, kind="silence")]
    assert ctx.metrics["cue_provider_used"] == "silence"


async def test_corrupt_cue_file_loads_as_none(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.cues_path.write_text("{not json")
    assert cues_step.load_cues(ctx) is None
