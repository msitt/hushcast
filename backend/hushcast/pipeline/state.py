"""Episode status state machine.

discovered -> queued -> downloading -> transcribing -> detecting -> cutting -> processed
Any active step may fail -> failed (retryable). Episodes that predate the
feed's addition are skipped. Skipped/failed can be (re)queued manually, and
failed episodes can be dismissed back to skipped ("give up on this one").
Retention cleanup moves processed -> expired.
Whitelisted feeds skip transcribe/detect/cut (download -> copy-through -> processed).
"""

DISCOVERED = "discovered"
SKIPPED = "skipped"
QUEUED = "queued"
DOWNLOADING = "downloading"
TRANSCRIBING = "transcribing"
DETECTING = "detecting"
CUTTING = "cutting"
PROCESSED = "processed"
FAILED = "failed"
EXPIRED = "expired"

ALL_STATUSES = {
    DISCOVERED, SKIPPED, QUEUED, DOWNLOADING, TRANSCRIBING,
    DETECTING, CUTTING, PROCESSED, FAILED, EXPIRED,
}

ACTIVE = {DOWNLOADING, TRANSCRIBING, DETECTING, CUTTING}
# States that should be re-queued after a restart (in-flight work was lost).
RESUMABLE = {QUEUED} | ACTIVE
TERMINAL = {PROCESSED, EXPIRED}

_TRANSITIONS: dict[str, set[str]] = {
    DISCOVERED: {QUEUED, SKIPPED},
    SKIPPED: {QUEUED},
    QUEUED: {DOWNLOADING, FAILED},
    DOWNLOADING: {TRANSCRIBING, CUTTING, FAILED, QUEUED},  # -> CUTTING for whitelisted copy-through
    TRANSCRIBING: {DETECTING, FAILED, QUEUED},
    DETECTING: {CUTTING, FAILED, QUEUED},
    CUTTING: {PROCESSED, FAILED, QUEUED},
    FAILED: {QUEUED, SKIPPED},  # SKIPPED = user dismisses the failure
    PROCESSED: {EXPIRED, QUEUED},  # QUEUED = explicit reprocess
    EXPIRED: {QUEUED},
}


class InvalidTransition(Exception):
    pass


def validate_transition(current: str, new: str) -> None:
    if new not in _TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"cannot transition episode from {current!r} to {new!r}")
