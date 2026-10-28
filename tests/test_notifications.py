"""Settings "Send test notification" action (notifications.send_test)."""
import json

import apprise
import pytest

from hushcast.notifications import send_test


class _FakeResult:
    """Stands in for Apprise v2's AppriseResult: truthiness carries success, not JSON-serializable."""

    def __init__(self, ok: bool):
        self._ok = ok

    def __bool__(self) -> bool:
        return self._ok


@pytest.mark.parametrize("ok", [True, False])
async def test_send_test_returns_plain_bool(monkeypatch, ok):
    async def fake_notify(self, **kwargs):
        return _FakeResult(ok)

    monkeypatch.setattr(apprise.Apprise, "async_notify", fake_notify)
    result, message = await send_test(["json://127.0.0.1/"])
    assert result is ok
    json.dumps({"ok": result, "message": message})


async def test_send_test_reports_unparseable_urls():
    result, message = await send_test(["json://127.0.0.1/", "notascheme://foo"])
    assert result is False
    assert message == "could not parse 1 of 2 URL(s), check the scheme/format"


async def test_send_test_without_urls():
    assert await send_test([]) == (False, "no notification URLs configured")
