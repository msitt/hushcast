"""Remote cue provider: an inaSpeechSegmenter-style HTTP service.

POSTs the audio file and expects segments labeled speech/music/noise/noEnergy
(inaSpeechSegmenter's vocabulary). Tolerates the two common response shapes:
a list of [label, start, stop] triples, or a list of objects with label and
start/stop (or start/end) fields, optionally nested under a "segments" key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .base import Cue, CueProvider

# speech-ish labels are dropped: the transcript is the authority on speech
_LABEL_MAP = {
    "music": "music",
    "noise": "noise",
    "noenergy": "noenergy",
    "silence": "silence",
}


def parse_ina_response(payload: Any) -> list[Cue]:
    if isinstance(payload, dict):
        payload = payload.get("segments", [])
    if not isinstance(payload, list):
        raise ValueError(f"unexpected cue service response shape: {type(payload).__name__}")
    cues: list[Cue] = []
    for item in payload:
        if isinstance(item, dict):
            label = item.get("label")
            start = item.get("start")
            end = item.get("stop", item.get("end"))
        elif isinstance(item, (list, tuple)) and len(item) >= 3:
            label, start, end = item[0], item[1], item[2]
        else:
            continue
        kind = _LABEL_MAP.get(str(label).lower())
        if kind is None:
            continue
        try:
            start_f, end_f = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if end_f > start_f:
            cues.append(Cue(start=start_f, end=end_f, kind=kind))
    return sorted(cues, key=lambda c: (c.start, c.end))


class InaClient(CueProvider):
    def __init__(self, *, base_url: str, timeout_s: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def detect(self, audio_path: Path) -> list[Cue]:
        if not self.base_url:
            raise RuntimeError("cue_remote_base_url is not configured")
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=30.0)) as client:
            with audio_path.open("rb") as fh:
                resp = await client.post(
                    f"{self.base_url}/segment",
                    files={"file": (audio_path.name, fh, "application/octet-stream")},
                )
        if resp.status_code != 200:
            raise RuntimeError(f"cue service failed: HTTP {resp.status_code}: {resp.text[:500]}")
        return parse_ina_response(resp.json())
