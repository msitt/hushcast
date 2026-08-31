"""Cue provider abstraction: non-speech time ranges in an episode's audio.

Speech regions are never emitted as cues. The transcript already covers them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# silence and noenergy both render as SILENCE in prompts, noise/music keep their names
CUE_KINDS = {"silence", "music", "noise", "noenergy"}


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    kind: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def cues_to_json(cues: list[Cue]) -> list[dict[str, Any]]:
    return [asdict(c) for c in cues]


def cues_from_json(data: list[dict[str, Any]]) -> list[Cue]:
    out = []
    for item in data:
        kind = str(item.get("kind", "silence"))
        if kind not in CUE_KINDS:
            continue
        out.append(Cue(start=float(item["start"]), end=float(item["end"]), kind=kind))
    return sorted(out, key=lambda c: (c.start, c.end))


class CueProvider(ABC):
    @abstractmethod
    async def detect(self, audio_path: Path) -> list[Cue]: ...
