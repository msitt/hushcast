"""Unit tests for podcast directory search normalization."""
from datetime import UTC, datetime

from hushcast.search import _Cache, normalize_itunes


def item(**over):
    base = {
        "collectionName": "The Daily",
        "artistName": "The New York Times",
        "feedUrl": "https://feeds.simplecast.com/54nAGcIl",
        "artworkUrl100": "https://img/100.jpg",
        "artworkUrl600": "https://img/600.jpg",
        "primaryGenreName": "Daily News",
        "trackCount": 2400,
        "releaseDate": "2026-09-03T09:00:00Z",
        "collectionExplicitness": "cleaned",
    }
    base.update(over)
    return base


def test_normalizes_fields():
    [r] = normalize_itunes({"results": [item()]})
    assert r.title == "The Daily"
    assert r.author == "The New York Times"
    assert r.feed_url == "https://feeds.simplecast.com/54nAGcIl"
    assert r.artwork_url == "https://img/600.jpg"
    assert r.genre == "Daily News"
    assert r.episode_count == 2400
    assert r.latest_episode_at == datetime(2026, 9, 3, 9, 0, tzinfo=UTC)
    assert r.explicit is False
    assert r.feed_host == "feeds.simplecast.com"


def test_drops_entries_without_feed_url():
    raw = {"results": [item(feedUrl=None), item(feedUrl=""), {"collectionName": "x"}]}
    assert normalize_itunes(raw) == []


def test_dedups_by_feed_url():
    raw = {"results": [item(collectionName="A"), item(collectionName="B")]}
    results = normalize_itunes(raw)
    assert [r.title for r in results] == ["A"]


def test_tolerates_missing_and_odd_fields():
    raw = {
        "results": [
            item(
                artistName=None,
                artworkUrl600=None,
                artworkUrl100=None,
                primaryGenreName=None,
                trackCount=0,
                releaseDate="not a date",
                collectionExplicitness="explicit",
                feedUrl="http://www.example.com/rss",
            ),
            "garbage",
        ]
    }
    [r] = normalize_itunes(raw)
    assert r.author is None
    assert r.artwork_url is None
    assert r.genre is None
    assert r.episode_count is None
    assert r.latest_episode_at is None
    assert r.explicit is True
    assert r.feed_host == "example.com"


def test_falls_back_to_small_artwork():
    [r] = normalize_itunes({"results": [item(artworkUrl600=None)]})
    assert r.artwork_url == "https://img/100.jpg"


def test_empty_payload():
    assert normalize_itunes({}) == []
    assert normalize_itunes({"results": None}) == []


def test_cache_evicts_and_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("hushcast.search.time.monotonic", lambda: now[0])
    cache = _Cache(size=2, ttl_s=10)
    cache.put("a", [])
    cache.put("b", [])
    cache.get("a")  # touch a so b is the LRU entry
    cache.put("c", [])
    assert cache.get("b") is None
    assert cache.get("a") == []
    now[0] += 11
    assert cache.get("a") is None
