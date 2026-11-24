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
    # non-speech music ranges the provider reported alongside the words, the
    # cues step turns them into music cues
    music: list[tuple[float, float]] = field(default_factory=list)

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
        music = [(float(a), float(b)) for a, b in data.get("music") or []]
        return cls(
            language=data.get("language"), duration=data.get("duration", 0.0), segments=segments, music=music
        )

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

        A segment that crosses an owned-range boundary is cut there by its word
        timestamps, each slice keeping the words it owns. Segments can be far
        longer than the overlap (a provider that returns one segment per slice),
        so keeping or dropping them whole would duplicate or lose minutes of
        speech. Without usable word timestamps the segment goes to the slice
        that owns its start.
        """
        language = next((t.language for t in transcripts if t.language), None)
        segments: list[TranscriptSegment] = []
        music: list[tuple[float, float]] = []
        for i, (transcript, offset) in enumerate(zip(transcripts, offsets, strict=True)):
            owned_start, owned_end = owned_ranges[i] if owned_ranges else (float("-inf"), float("inf"))
            # music is clipped to the slice's owned range, and a range cut in two
            # by a slice boundary is joined again
            for a, b in transcript.music:
                a, b = max(a + offset, owned_start), min(b + offset, owned_end)
                if b <= a:
                    continue
                if music and a - music[-1][1] <= 0.5:
                    music[-1] = (music[-1][0], max(music[-1][1], b))
                else:
                    music.append((a, b))
            for seg in transcript.segments:
                start = seg.start + offset
                words = [
                    Word(text=w.text, start=w.start + offset, end=w.end + offset, speaker=w.speaker)
                    for w in seg.words
                ]
                tokens = seg.text.split()
                owned = [i for i, w in enumerate(words) if owned_start <= w.start < owned_end]
                if words and len(words) == len(tokens) and len(owned) < len(words):
                    # crosses a boundary: keep only the words this slice owns
                    if not owned:
                        continue
                    kept = words[owned[0] : owned[-1] + 1]
                    segments.append(
                        TranscriptSegment(
                            text=" ".join(tokens[owned[0] : owned[-1] + 1]),
                            start=kept[0].start,
                            end=max(w.end for w in kept),
                            speaker=seg.speaker,
                            words=kept,
                        )
                    )
                    continue
                if not (owned_start <= start < owned_end):
                    continue
                segments.append(
                    TranscriptSegment(text=seg.text, start=start, end=seg.end + offset, speaker=seg.speaker, words=words)
                )
        duration = segments[-1].end if segments else 0.0
        return cls(language=language, duration=duration, segments=segments, music=music)


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
