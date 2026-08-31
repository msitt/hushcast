"""OpenAI-compatible transcription client.

Covers local or cloud endpoints that speak `POST {base_url}/audio/transcriptions`
with `response_format=verbose_json`.
Diarization/speaker fields are whisperx extensions, absence is tolerated.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from ..errors import RateLimitError, parse_retry_after
from .base import ProviderCapabilities, Transcript, TranscriptSegment, TranscriptionProvider, Word

log = logging.getLogger(__name__)

MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
}


def _distribute_words(segments: list[TranscriptSegment], raw_words: list[Any]) -> None:
    """Assign a payload-level word list to segments by time (word midpoint).

    A word whose midpoint falls between two segments (timestamp drift) goes to
    whichever segment edge is nearer.
    """
    words = [
        Word(
            text=w.get("word") or w.get("text") or "",
            start=float(w["start"]),
            end=float(w.get("end", w["start"])),
            speaker=w.get("speaker"),
        )
        for w in raw_words
        if isinstance(w, dict) and w.get("start") is not None
    ]
    if not words or not segments:
        return
    ordered = sorted(segments, key=lambda s: (s.start, s.end))
    i = 0
    for w in sorted(words, key=lambda w: (w.start, w.end)):
        mid = (w.start + w.end) / 2
        while i + 1 < len(ordered) and ordered[i].end < mid:
            i += 1
        # mid may sit in the gap before ordered[i], the previous segment wins
        # when its trailing edge is nearer
        if i > 0 and mid < ordered[i].start and mid - ordered[i - 1].end < ordered[i].start - mid:
            ordered[i - 1].words.append(w)
        else:
            ordered[i].words.append(w)


class OpenAICompatTranscriber(TranscriptionProvider):
    def __init__(self, settings: dict[str, Any]):
        self.base_url = settings["transcription_base_url"].rstrip("/")
        self.api_key = settings["transcription_api_key"]
        self.model = settings["transcription_model"]
        self.word_timestamps = bool(settings["transcription_word_timestamps"])
        self.diarize = bool(settings["transcription_diarize"])
        self.timeout_s = float(settings["transcription_timeout_s"])
        self.extra_params = settings["transcription_extra_params"] or {}
        # raw provider response of the most recent transcribe() call, kept for
        # the per-call debug log written by the transcribe step
        self.last_raw_payload: dict[str, Any] | None = None

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_word_timestamps=True, supports_diarization=True)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _endpoint(self) -> str:
        return f"{self.base_url}/audio/transcriptions"

    async def transcribe(self, audio_path: Path) -> Transcript:
        if not self.base_url:
            raise RuntimeError("transcription_base_url is not configured")

        data: dict[str, Any] = {"model": self.model, "response_format": "verbose_json"}
        granularities = ["segment"]
        if self.word_timestamps:
            granularities.append("word")
        data["timestamp_granularities[]"] = granularities
        if self.diarize:
            data["diarize"] = "true"
        for k, v in self.extra_params.items():
            data[k] = v if isinstance(v, list) else str(v)

        mime = MIME_BY_EXT.get(audio_path.suffix.lower(), "application/octet-stream")
        log.info(
            "transcribe: uploading %s (%.1f MB) model=%s params=%s",
            audio_path.name,
            audio_path.stat().st_size / 1_000_000,
            self.model,
            {k: v for k, v in data.items() if k != "model"},
        )
        timeout = httpx.Timeout(connect=30.0, read=self.timeout_s, write=self.timeout_s, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            with audio_path.open("rb") as f:
                resp = await client.post(
                    self._endpoint(),
                    headers=self._headers(),
                    data=data,
                    files={"file": (audio_path.name, f, mime)},
                )
        if resp.status_code == 429:
            raise RateLimitError(
                f"transcription provider rate limited us: HTTP 429: {resp.text[:500]}",
                retry_after=parse_retry_after(resp.headers),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"transcription failed: HTTP {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        self.last_raw_payload = payload
        return self._normalize(payload)

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> Transcript:
        raw_segments = payload.get("segments") or []
        # OpenAI/Groq put word timestamps in a top-level "words" array, not
        # nested per segment, whisperx-api-server nests the aligned result:
        # {"segments": {"segments": [...], "word_segments": [...]}}.
        top_words = payload.get("words")
        if isinstance(raw_segments, dict):
            top_words = top_words or raw_segments.get("word_segments")
            raw_segments = raw_segments.get("segments") or []

        segments: list[TranscriptSegment] = []
        for s in raw_segments:
            if not isinstance(s, dict):
                continue
            words = [
                Word(
                    text=w.get("word") or w.get("text") or "",
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    speaker=w.get("speaker"),
                )
                for w in (s.get("words") or [])
                if isinstance(w, dict) and w.get("start") is not None
            ]
            segments.append(
                TranscriptSegment(
                    text=(s.get("text") or "").strip(),
                    start=float(s.get("start", 0.0)),
                    end=float(s.get("end", 0.0)),
                    speaker=s.get("speaker"),
                    words=words,
                )
            )
        if top_words and not any(s.words for s in segments):
            _distribute_words(segments, top_words)

        # Some providers echo a placeholder speaker (e.g. "SPEAKER_00") on every
        # segment even when they didn't actually diarize. A single distinct
        # speaker across the whole transcript carries no information, so strip
        # it rather than waste LLM tokens tagging every line identically.
        distinct_speakers = {s.speaker for s in segments if s.speaker}
        if len(distinct_speakers) <= 1:
            for s in segments:
                s.speaker = None
                for w in s.words:
                    w.speaker = None

        duration = payload.get("duration")
        if not duration and segments:
            duration = segments[-1].end
        return Transcript(language=payload.get("language"), duration=float(duration or 0.0), segments=segments)

    async def health_check(self) -> None:
        if not self.base_url:
            raise RuntimeError("transcription_base_url is not configured")
        root = self.base_url.removesuffix("/v1")
        last: Exception | None = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Authenticated endpoints first (whisperx /info, generic /v1/models) so a
            # bad API key fails the test, whisperx /healthcheck is deliberately
            # unauthenticated and only proves reachability, so it comes last.
            for url in (f"{root}/info", f"{root}/v1/models", f"{root}/healthcheck"):
                try:
                    resp = await client.get(url, headers=self._headers())
                except httpx.HTTPError as exc:
                    last = exc
                    continue
                if resp.status_code in (401, 403):
                    raise RuntimeError(
                        f"server reachable but rejected the API key (HTTP {resp.status_code} on {url})"
                    )
                if resp.status_code < 400:
                    return
            raise RuntimeError(f"transcription server unreachable: {last or 'no endpoint responded'}")


def build_transcriber(settings: dict[str, Any]) -> TranscriptionProvider:
    return OpenAICompatTranscriber(settings)
