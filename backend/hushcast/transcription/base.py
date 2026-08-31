"""Transcription provider abstraction."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Word:
    text: str
    start: float
    end: float
    speaker: str | None = None


@dataclass
class TranscriptSegment:
    text: str
    start: float
    end: float
    speaker: str | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str | None
    duration: float
    segments: list[TranscriptSegment]

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> Transcript:
        segments = [
            TranscriptSegment(
                text=s["text"],
                start=s["start"],
                end=s["end"],
                speaker=s.get("speaker"),
                words=[Word(**w) for w in s.get("words", [])],
            )
            for s in data.get("segments", [])
        ]
        return cls(language=data.get("language"), duration=data.get("duration", 0.0), segments=segments)

    @classmethod
    def concat(
        cls,
        transcripts: list[Transcript],
        offsets: list[float],
        owned_ranges: list[tuple[float, float]] | None = None,
    ) -> Transcript:
        """Merge transcripts of consecutive audio slices into one, shifting each
        slice's segment/word timestamps by its start offset in the original audio.

        When slices were split with overlap (see `ffmpeg.split_by_duration`), pass
        `owned_ranges` (one (start, end) pair per slice, in the original audio's
        timeline) to keep only the segments each slice is authoritative for. This
        drops the duplicate, edge-degraded transcription of the overlap that its
        neighbor also produced, keeping just the copy from whichever slice has
        that stretch of audio away from its own edge.
        """
        language = next((t.language for t in transcripts if t.language), None)
        segments: list[TranscriptSegment] = []
        for i, (transcript, offset) in enumerate(zip(transcripts, offsets, strict=True)):
            owned_start, owned_end = owned_ranges[i] if owned_ranges else (float("-inf"), float("inf"))
            for seg in transcript.segments:
                start = seg.start + offset
                if not (owned_start <= start < owned_end):
                    continue
                segments.append(
                    TranscriptSegment(
                        text=seg.text,
                        start=start,
                        end=seg.end + offset,
                        speaker=seg.speaker,
                        words=[
                            Word(text=w.text, start=w.start + offset, end=w.end + offset, speaker=w.speaker)
                            for w in seg.words
                        ],
                    )
                )
        duration = segments[-1].end if segments else 0.0
        return cls(language=language, duration=duration, segments=segments)


@dataclass
class ProviderCapabilities:
    supports_word_timestamps: bool = True
    supports_diarization: bool = False


class TranscriptionProvider(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    async def transcribe(self, audio_path: Path) -> Transcript: ...

    @abstractmethod
    async def health_check(self) -> None:
        """Raise on failure. Return None when the provider is reachable."""
