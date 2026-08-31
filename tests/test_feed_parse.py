from pathlib import Path

from hushcast.rss.fetch import parse_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_parse_sample_feed():
    parsed = parse_bytes(FIXTURE.read_bytes())
    assert parsed.title == "Test Podcast"
    assert parsed.image_url == "https://example.com/cover.jpg"
    assert parsed.author == "Test Author"
    # the enclosure-less item is dropped
    assert len(parsed.episodes) == 3


def test_length_zero_becomes_none():
    parsed = parse_bytes(FIXTURE.read_bytes())
    ep3 = next(e for e in parsed.episodes if e.guid == "guid-ep3")
    assert ep3.enclosure_length is None
    assert ep3.duration_s == 3750.0  # 1:02:30


def test_missing_guid_falls_back_to_enclosure_url():
    parsed = parse_bytes(FIXTURE.read_bytes())
    ep2 = next(e for e in parsed.episodes if "ep2" in e.enclosure_url)
    assert ep2.guid == "https://cdn.example.com/ep2.m4a"
    assert ep2.duration_s == 3600.0


def test_multiple_enclosures_picks_audio():
    parsed = parse_bytes(FIXTURE.read_bytes())
    ep1 = next(e for e in parsed.episodes if e.guid == "guid-ep1")
    assert ep1.enclosure_url.endswith("ep1.mp3")
    assert ep1.enclosure_type == "audio/mpeg"
