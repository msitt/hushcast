"""Integration tests for ffmpeg helpers (skipped when ffmpeg is unavailable)."""
import shutil
import subprocess

import pytest

from hushcast.audio import ffmpeg

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def tone_mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
         "-c:a", "libmp3lame", "-q:a", "6", str(path)],
        check=True, capture_output=True,
    )
    return path


async def test_probe_duration(tone_mp3):
    duration = await ffmpeg.probe_duration(tone_mp3)
    assert 59 <= duration <= 61


async def test_cut_keep_ranges(tone_mp3, tmp_path):
    dest = tmp_path / "cut.mp3"
    await ffmpeg.cut(tone_mp3, dest, [(0.0, 10.0), (30.0, 40.0)])
    assert dest.exists()
    duration = await ffmpeg.probe_duration(dest)
    assert 19 <= duration <= 21  # 10s + 10s kept


async def test_cut_refuses_empty_keep(tone_mp3, tmp_path):
    with pytest.raises(ffmpeg.FfmpegError):
        await ffmpeg.cut(tone_mp3, tmp_path / "x.mp3", [])


async def test_copy_to_mp3_stream_copies_mp3(tone_mp3, tmp_path):
    dest = tmp_path / "copy.mp3"
    await ffmpeg.copy_to_mp3(tone_mp3, dest)
    duration = await ffmpeg.probe_duration(dest)
    assert 59 <= duration <= 61


async def test_transcode_for_upload(tone_mp3, tmp_path):
    dest = tmp_path / "small.ogg"
    await ffmpeg.transcode_for_upload(tone_mp3, dest)
    assert dest.exists()
    duration = await ffmpeg.probe_duration(dest)
    assert 59 <= duration <= 61  # duration preserved, real speech shrinks ~10x at 24k mono opus


async def test_split_by_duration(tone_mp3, tmp_path):
    parts = await ffmpeg.split_by_duration(tone_mp3, tmp_path, "tone", ".mp3", 25.0, 60.0)
    assert [round(p.owned_start) for p in parts] == [0, 25, 50]
    assert [round(p.owned_end) for p in parts] == [25, 50, 60]
    assert [round(p.content_start) for p in parts] == [0, 25, 50]  # no overlap requested
    durations = [await ffmpeg.probe_duration(p.path) for p in parts]
    assert durations[0] == pytest.approx(25, abs=1)
    assert durations[1] == pytest.approx(25, abs=1)
    assert durations[2] == pytest.approx(10, abs=1)  # final part is the remainder


async def test_split_by_duration_with_overlap(tone_mp3, tmp_path):
    parts = await ffmpeg.split_by_duration(tone_mp3, tmp_path, "tone", ".mp3", 25.0, 60.0, overlap_seconds=8.0)
    assert [round(p.owned_start) for p in parts] == [0, 25, 50]
    assert [round(p.owned_end) for p in parts] == [25, 50, 60]
    # first part isn't extended backward (clamped at 0). Middle part is extended
    # on both sides. Last part isn't extended forward (clamped at total_duration)
    assert [round(p.content_start) for p in parts] == [0, 17, 42]
    durations = [await ffmpeg.probe_duration(p.path) for p in parts]
    assert durations[0] == pytest.approx(33, abs=1)  # 25 owned + 8 trailing overlap
    assert durations[1] == pytest.approx(41, abs=1)  # 25 owned + 8 leading + 8 trailing
    assert durations[2] == pytest.approx(18, abs=1)  # 10 owned + 8 leading overlap
