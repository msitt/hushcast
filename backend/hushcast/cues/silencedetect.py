"""Built-in cue provider: ffmpeg silencedetect (silence-only, no extra deps)."""
from __future__ import annotations

import re
from pathlib import Path

from ..audio import ffmpeg
from .base import Cue, CueProvider

_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silencedetect(stderr: str, total_duration: float | None = None) -> list[tuple[float, float]]:
    """Extract (start, end) silence ranges from ffmpeg silencedetect stderr.

    A trailing `silence_start` with no matching `silence_end` (silence runs to
    end of file) is closed at `total_duration` when known, else dropped.
    """
    ranges: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        m = _START_RE.search(line)
        if m:
            open_start = max(0.0, float(m.group(1)))
            continue
        m = _END_RE.search(line)
        if m and open_start is not None:
            end = float(m.group(1))
            if end > open_start:
                ranges.append((open_start, end))
            open_start = None
    if open_start is not None and total_duration is not None and total_duration > open_start:
        ranges.append((open_start, total_duration))
    return ranges


class SilenceDetectProvider(CueProvider):
    def __init__(self, *, noise_db: float, min_silence_s: float):
        self.noise_db = noise_db
        self.min_silence_s = min_silence_s

    async def detect(self, audio_path: Path) -> list[Cue]:
        stderr = await ffmpeg.silencedetect_output(
            audio_path, noise_db=self.noise_db, min_silence_s=self.min_silence_s
        )
        duration = await ffmpeg.probe_duration(audio_path)
        return [Cue(start=s, end=e, kind="silence") for s, e in parse_silencedetect(stderr, duration)]
