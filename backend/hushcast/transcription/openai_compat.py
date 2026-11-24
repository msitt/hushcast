"""OpenAI-compatible transcription client.

Covers local or cloud endpoints that speak `POST {base_url}/audio/transcriptions`
with `response_format=verbose_json`.
Diarization/speaker fields are whisperx extensions, absence is tolerated.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from ..detection.llm import reported_cost
from ..errors import RateLimitError, parse_retry_after
from .base import (
    ProviderCapabilities,
    Transcript,
    TranscriptionProvider,
    TranscriptSegment,
    Word,
)

log = logging.getLogger(__name__)

MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".flac": "audio/flac",
}


# A provider segment longer than this is not a usable line (ElevenLabs Scribe
# answers with one segment for the whole file), so lines are built from the
# word timestamps instead.
COARSE_SEGMENT_S = 60.0

# A pause this long between two words starts a new line when lines are built
# from words, so no line stretches across music or dead air.
WORD_GAP_BREAK_S = 2.0


def _speaker_label(value: Any) -> str | None:
    """Speaker label as text. Providers that number speakers start at 0, which
    must not read as "no speaker"."""
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return f"SPEAKER_{value:02d}"
    return str(value)


def _split_audio_events(raw_words: list[Any]) -> tuple[list[Any], list[tuple[float, float]]]:
    """Separate spoken words from sound tags.

    Scribe lists non-speech sounds among the words, as entries like
    `{"word": "[upbeat music]", "type": "audio_event"}`. They are not speech, so
    they leave the transcript. The music ones come back as time ranges, which
    the cues step turns into music cues.
    """
    spoken: list[Any] = []
    music: list[tuple[float, float]] = []
    for w in raw_words:
        if not (isinstance(w, dict) and w.get("type") == "audio_event"):
            spoken.append(w)
            continue
        label = str(w.get("word") or w.get("text") or "").lower()
        if "music" in label and w.get("start") is not None:
            start = float(w["start"])
            end = float(w.get("end", start))
            if end > start:
                music.append((start, end))
    return spoken, music


def _parse_words(raw_words: list[Any]) -> list[Word]:
    """Word objects from a provider's raw word list, skipping unusable entries."""
    return [
        Word(
            text=w.get("word") or w.get("text") or "",
            start=float(w["start"]),
            end=float(w.get("end", w["start"])),
            speaker=_speaker_label(w.get("speaker")),
        )
        for w in raw_words
        if isinstance(w, dict) and w.get("start") is not None
    ]


def _segment_from_run(run: list[Word]) -> TranscriptSegment:
    return TranscriptSegment(
        text=" ".join(w.text for w in run),
        start=run[0].start,
        end=max(w.end for w in run),
        speaker=run[0].speaker,
        words=list(run),
    )


def _segments_from_words(raw_words: list[Any]) -> list[TranscriptSegment]:
    """Build segments for a provider that times words but returns no segments.

    Gemini-style transcription endpoints answer with a flat word array and no
    segmentation at all, which leaves detection with no boundary to cut at. One
    segment per run of consecutive same-speaker words, broken at long pauses,
    gives it something real, and sentence splitting divides those further. The synthesized text is joined
    from the same words it carries, so the two align token for token and the
    split lands on exact word timings rather than prorated ones.
    """
    words = [w for w in _parse_words(raw_words) if w.text]
    if not words:
        return []
    segments: list[TranscriptSegment] = []
    run: list[Word] = []
    for w in sorted(words, key=lambda w: (w.start, w.end)):
        if run and (w.speaker != run[0].speaker or w.start - run[-1].end >= WORD_GAP_BREAK_S):
            segments.append(_segment_from_run(run))
            run = []
        run.append(w)
    segments.append(_segment_from_run(run))
    return segments


def _distribute_words(segments: list[TranscriptSegment], raw_words: list[Any]) -> None:
    """Assign a payload-level word list to segments by time (word midpoint).

    A word whose midpoint falls between two segments (timestamp drift) goes to
    whichever segment edge is nearer.
    """
    words = _parse_words(raw_words)
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
        # what the provider said the most recent call cost in USD, None when it did not say
        self.last_cost_usd: float | None = None

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
            data[k] = self._form_value(v)

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
        self.last_cost_usd = reported_cost(payload, resp.headers)
        return self._normalize(payload)

    @staticmethod
    def _form_value(value: Any) -> Any:
        """One extra param as a multipart form value.

        A list becomes a repeated field. An object goes out as JSON text, which
        is how OpenRouter takes `provider` options in a form upload.
        """
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return json.dumps(value)
        return str(value)

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
        music: list[tuple[float, float]] = []
        if isinstance(top_words, list):
            top_words, music = _split_audio_events(top_words)

        segments: list[TranscriptSegment] = []
        for s in raw_segments:
            if not isinstance(s, dict):
                continue
            words = [
                Word(
                    text=w.get("word") or w.get("text") or "",
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    speaker=_speaker_label(w.get("speaker")),
                )
                for w in (s.get("words") or [])
                if isinstance(w, dict) and w.get("start") is not None
            ]
            segments.append(
                TranscriptSegment(
                    text=(s.get("text") or "").strip(),
                    start=float(s.get("start", 0.0)),
                    end=float(s.get("end", 0.0)),
                    speaker=_speaker_label(s.get("speaker")),
                    words=words,
                )
            )
        coarse = any(s.end - s.start > COARSE_SEGMENT_S for s in segments)
        if coarse and len(segments) == 1 and not top_words and not segments[0].words:
            raise RuntimeError(
                f"transcription returned one segment of {segments[0].end - segments[0].start:.0f}s and no "
                "word timestamps, so lines cannot be timed. Turn on word timestamps for this provider."
            )
        if top_words and (not segments or coarse):
            segments = _segments_from_words(top_words)
        elif top_words and not any(s.words for s in segments):
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
        return Transcript(
            language=payload.get("language"), duration=float(duration or 0.0), segments=segments, music=music
        )

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
