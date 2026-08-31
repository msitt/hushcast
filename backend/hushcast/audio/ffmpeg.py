"""ffmpeg/ffprobe helpers: probe, cut keep-ranges, transcode for upload."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class FfmpegError(RuntimeError):
    pass


@dataclass
class SplitPart:
    path: Path
    content_start: float  # this file's start offset in the original audio's timeline
    owned_start: float  # start of the range (original timeline) this part is authoritative for
    owned_end: float  # end of that authoritative range (exclusive)


def _run_sync(*args: str, return_stderr: bool = False) -> str:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise FfmpegError(
            f"{args[0]} failed (exit {proc.returncode}): {proc.stderr.decode(errors='replace')[-2000:]}"
        )
    stream = proc.stderr if return_stderr else proc.stdout
    return stream.decode(errors="replace")


async def _run(*args: str, return_stderr: bool = False) -> str:
    # Blocking subprocess in a thread rather than asyncio.create_subprocess_exec:
    # Windows SelectorEventLoop (uvicorn's default there) doesn't implement
    # asyncio subprocesses, and this works identically on every loop/platform.
    return await asyncio.to_thread(lambda: _run_sync(*args, return_stderr=return_stderr))


async def probe(path: Path) -> dict:
    out = await _run(
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    )
    return json.loads(out)


async def probe_duration(path: Path) -> float:
    info = await probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FfmpegError(f"could not determine duration of {path}") from exc


async def has_cover_art(path: Path) -> bool:
    info = await probe(path)
    return any(s.get("codec_type") == "video" for s in info.get("streams", []))


async def cut(
    source: Path,
    dest: Path,
    keep: list[tuple[float, float]],
    *,
    mp3_quality: int = 4,
) -> None:
    """Re-encode `source` to MP3 at `dest`, keeping only the given time ranges."""
    if not keep:
        raise FfmpegError("refusing to cut: no keep-ranges (entire episode would be removed)")

    tmp = dest.with_suffix(".tmp.mp3")
    filters = []
    for i, (start, end) in enumerate(keep):
        filters.append(f"[0:a]atrim={start:.3f}:{end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    inputs = "".join(f"[a{i}]" for i in range(len(keep)))
    filters.append(f"{inputs}concat=n={len(keep)}:v=0:a=1[out]")
    filtergraph = ";".join(filters)

    args = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter_complex", filtergraph, "-map", "[out]",
    ]
    if await has_cover_art(source):
        args += ["-map", "0:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    args += [
        "-map_metadata", "0", "-id3v2_version", "3",
        "-c:a", "libmp3lame", "-q:a", str(mp3_quality),
        str(tmp),
    ]
    try:
        await _run(*args)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


async def copy_to_mp3(source: Path, dest: Path, *, mp3_quality: int = 4) -> None:
    """Produce dest MP3 from source without cutting (no ads / whitelisted feed).

    Stream-copies when the source is already MP3, otherwise re-encodes.
    """
    tmp = dest.with_suffix(".tmp.mp3")
    info = await probe(source)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio and audio.get("codec_name") == "mp3":
        codec_args = ["-c", "copy"]
    else:
        codec_args = ["-vn", "-c:a", "libmp3lame", "-q:a", str(mp3_quality)]
        if audio and await has_cover_art(source):
            codec_args = ["-map", "0:a:0", "-map", "0:v:0", "-c:v", "copy",
                          "-disposition:v:0", "attached_pic",
                          "-c:a", "libmp3lame", "-q:a", str(mp3_quality)]
    try:
        await _run("ffmpeg", "-y", "-i", str(source), *codec_args, "-id3v2_version", "3", str(tmp))
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


async def silencedetect_output(source: Path, *, noise_db: float, min_silence_s: float) -> str:
    """Raw stderr of ffmpeg's silencedetect filter (that's where it logs).

    Parsing lives in cues/silencedetect.py so it stays a pure, testable function.
    """
    return await _run(
        "ffmpeg", "-nostats", "-i", str(source),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
        return_stderr=True,
    )


async def transcode_for_upload(source: Path, dest: Path) -> None:
    """16 kHz mono Opus, often small enough for size-capped transcription endpoints."""
    tmp = dest.with_suffix(".tmp.ogg")
    try:
        await _run(
            "ffmpeg", "-y", "-i", str(source), "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
            str(tmp),
        )
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


async def split_by_duration(
    source: Path,
    dest_dir: Path,
    stem: str,
    ext: str,
    part_seconds: float,
    total_duration: float,
    overlap_seconds: float = 0.0,
) -> list[SplitPart]:
    """Split `source` into consecutive parts, stream-copying (no re-encode).

    Each part "owns" a `part_seconds`-wide, non-overlapping slice of the
    timeline ([owned_start, owned_end)), but its actual audio content is
    extended by `overlap_seconds` into each neighbor (clamped at the ends of
    the source). This means a word that straddles an owned-range boundary is
    never truncated at the very edge of every file it appears in. Whichever
    part owns that boundary has real audio on both sides of it to transcribe
    from, while its neighbor's copy of that overlap is discarded on merge.
    """
    parts: list[SplitPart] = []
    owned_start = 0.0
    idx = 0
    while owned_start < total_duration - 0.01:
        idx += 1
        owned_end = min(owned_start + part_seconds, total_duration)
        content_start = max(0.0, owned_start - overlap_seconds)
        content_end = min(total_duration, owned_end + overlap_seconds)
        part_path = dest_dir / f"{stem}.part{idx}{ext}"
        tmp = part_path.with_suffix(f".tmp{ext}")
        try:
            await _run(
                "ffmpeg", "-y", "-ss", f"{content_start:.3f}", "-i", str(source),
                "-t", f"{content_end - content_start:.3f}", "-c", "copy", str(tmp),
            )
            tmp.replace(part_path)
        finally:
            tmp.unlink(missing_ok=True)
        parts.append(SplitPart(path=part_path, content_start=content_start, owned_start=owned_start, owned_end=owned_end))
        owned_start = owned_end
    return parts
