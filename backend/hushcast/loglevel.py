"""Runtime log-level control.

The DB setting `log_level` is the normal source of truth: editable from the
System page and applied immediately, no restart. HUSHCAST_LOG_LEVEL, when set,
is an explicit override that always wins. It exists for debugging setups
where the DB or UI can't be reached, so a stored value must not mask it.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import get_config

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Third-party loggers reined in at DEBUG so it stays focused on hushcast's own
# logs (LLM payloads etc.). sqlalchemy.engine echoes SQL at INFO, aiosqlite
# logs cursor ops at DEBUG.
NOISY = ("sqlalchemy", "aiosqlite", "httpcore", "apscheduler", "python_multipart", "urllib3")


def effective(settings: dict[str, Any]) -> str:
    config = get_config()
    return (config.log_level or settings["log_level"]).upper()


def current() -> str:
    return logging.getLevelName(logging.getLogger().level)


def apply(level: str) -> None:
    level = level.upper()
    logging.getLogger().setLevel(level)
    if level == "DEBUG":
        logging.getLogger("httpx").setLevel(logging.INFO)  # one line per request is useful
        for noisy in NOISY:
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        for name in ("httpx", *NOISY):
            logging.getLogger(name).setLevel(logging.NOTSET)  # inherit root again
