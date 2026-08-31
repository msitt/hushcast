"""Audio-cue providers: classify non-speech regions (silence/music) of an episode."""
from __future__ import annotations

from typing import Any

from .base import Cue, CueProvider
from .ina_client import InaClient
from .silencedetect import SilenceDetectProvider

__all__ = ["Cue", "CueProvider", "InaClient", "SilenceDetectProvider", "build_cue_provider"]


def build_cue_provider(settings: dict[str, Any]) -> CueProvider | None:
    """Provider selected by the `cue_provider` setting, None when disabled."""
    kind = str(settings.get("cue_provider") or "off")
    if kind == "remote":
        return InaClient(
            base_url=str(settings.get("cue_remote_base_url") or ""),
            timeout_s=float(settings.get("cue_remote_timeout_s") or 300),
        )
    if kind == "silence":
        return SilenceDetectProvider(
            noise_db=float(settings.get("cue_silence_noise_db") or -35.0),
            min_silence_s=float(settings.get("cue_min_silence_s") or 1.0),
        )
    return None
