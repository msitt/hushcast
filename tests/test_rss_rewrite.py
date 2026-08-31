from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from hushcast.models import Episode, Feed
from hushcast.rss.rewrite import ITUNES_NS, build_feed_xml


def make_feed():
    return Feed(
        id=1, slug="test-show", source_url="https://example.com/feed.xml",
        title="Test Show", description="Desc", image_url="https://example.com/cover.jpg",
        author="Author", link="https://example.com",
    )


def make_episode(i=1, **kw):
    defaults = dict(
        id=i, feed_id=1, guid=f"guid-{i}", title=f"Ep {i}",
        published_at=datetime(2025, 8, i, 10, 0, tzinfo=UTC),
        description_html="<p>notes</p>",
        source_enclosure_url="https://cdn.example.com/x.mp3",
        processed_bytes=1234567, processed_duration_s=3725.0,
    )
    defaults.update(kw)
    return Episode(**defaults)


def render(feed, episodes):
    xml = build_feed_xml(feed, episodes, public_url="https://hush.example.com/", token="tok123")
    return ET.fromstring(xml)


def test_channel_metadata():
    root = render(make_feed(), [])
    channel = root.find("channel")
    assert channel.findtext("title") == "[hushcast] Test Show"
    assert channel.find(f"{{{ITUNES_NS}}}image").get("href") == "https://example.com/cover.jpg"


def test_item_guid_is_original_and_not_permalink():
    root = render(make_feed(), [make_episode(1)])
    guid = root.find("channel/item/guid")
    assert guid.text == "guid-1"
    assert guid.get("isPermaLink") == "false"


def test_enclosure_url_length_type():
    root = render(make_feed(), [make_episode(1)])
    enc = root.find("channel/item/enclosure")
    assert enc.get("url") == "https://hush.example.com/p/tok123/audio/1.mp3"
    assert enc.get("length") == "1234567"
    assert enc.get("type") == "audio/mpeg"


def test_itunes_duration_format():
    root = render(make_feed(), [make_episode(1)])
    assert root.findtext(f"channel/item/{{{ITUNES_NS}}}duration") == "1:02:05"


def test_multiple_items_order_preserved():
    root = render(make_feed(), [make_episode(2), make_episode(1)])
    titles = [i.findtext("title") for i in root.findall("channel/item")]
    assert titles == ["Ep 2", "Ep 1"]


def test_pubdate_rfc2822():
    root = render(make_feed(), [make_episode(1)])
    assert "Aug 2025" in root.findtext("channel/item/pubDate")
