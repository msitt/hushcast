"""Shared context passed through pipeline steps for one episode."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import AppConfig


@dataclass
class EpisodeContext:
    episode_id: int
    feed_id: int
    episode_title: str
    podcast_title: str
    guid: str
    source_enclosure_url: str
    whitelisted: bool
    detection_hints: str | None
    learned_hints: str | None
    config: AppConfig
    settings: dict[str, Any]
    log: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    # populated as steps run
    original_path: Path | None = None
    duration_s: float | None = None

    @property
    def processed_path(self) -> Path:
        return self.config.processed_audio_dir / f"{self.episode_id}.mp3"

    @property
    def transcript_path(self) -> Path:
        return self.config.transcripts_dir / f"{self.episode_id}.json"

    @property
    def raw_transcript_path(self) -> Path:
        return self.config.transcripts_dir / f"{self.episode_id}.raw.json"

    @property
    def cues_path(self) -> Path:
        return self.config.cues_dir / f"{self.episode_id}.json"
