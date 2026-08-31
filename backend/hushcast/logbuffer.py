"""In-memory ring buffer of recent log records, served on the System page.

Deliberately not persisted: it resets on restart, and `docker logs` remains the
authoritative log source. The handler is attached to the root logger in
main.py's lifespan, so it captures whatever the configured log level allows.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime

CAPACITY = 1000


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = CAPACITY):
        super().__init__(level=logging.DEBUG)
        self.capacity = capacity
        self.records: deque[dict] = deque(maxlen=capacity)
        # %(message)s still appends formatted exception tracebacks
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.records.append(
            {
                "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "levelno": record.levelno,
                "logger": record.name,
                "message": message,
            }
        )

    def tail(self, limit: int = 200, min_level: int = logging.NOTSET) -> list[dict]:
        """Most recent `limit` records at or above `min_level`, oldest first."""
        matched: list[dict] = []
        for rec in reversed(self.records):
            if rec["levelno"] >= min_level:
                matched.append(rec)
                if len(matched) >= limit:
                    break
        matched.reverse()
        return matched


handler = RingBufferHandler()
