"""Shared exception types for provider clients and pipeline steps."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from time import time


class RateLimitError(RuntimeError):
    """Raised when a provider responds with HTTP 429 (rate limited).

    `retry_after` is the number of seconds the provider asked us to wait,
    parsed from the Retry-After response header, when present.
    """

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def parse_retry_after(headers) -> float | None:
    """Parse a Retry-After header value (seconds, or an HTTP-date) into seconds."""
    value = headers.get("retry-after")
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time())
