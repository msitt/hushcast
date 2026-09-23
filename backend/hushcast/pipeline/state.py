"""Episode status state machine.

discovered -> queued -> downloading -> transcribing -> detecting -> cutting -> processed
Any active step may fail -> failed (retryable). Episodes that predate the
feed's addition are skipped. Skipped/failed can be (re)queued manually, and
failed episodes can be dismissed back to skipped ("give up on this one").
Detection can end in review (the result failed a sanity check and a human
has to decide, never auto-retried) or skipped (the episode is promo-only and
the settings say to drop it). Retention cleanup moves processed -> expired.
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
REVIEW = "review"
EXPIRED = "expired"

ALL_STATUSES = {
    DISCOVERED, SKIPPED, QUEUED, DOWNLOADING, TRANSCRIBING,
    DETECTING, CUTTING, PROCESSED, FAILED, REVIEW, EXPIRED,
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
    # -> REVIEW when the result fails a sanity check, -> SKIPPED for dropped promo-only episodes
    DETECTING: {CUTTING, FAILED, QUEUED, REVIEW, SKIPPED},
    CUTTING: {PROCESSED, FAILED, QUEUED},
    FAILED: {QUEUED, SKIPPED},  # SKIPPED = user dismisses the failure
    REVIEW: {QUEUED, SKIPPED},  # QUEUED = re-detect or cut as reviewed, SKIPPED = dismiss
    PROCESSED: {EXPIRED, QUEUED},  # QUEUED = explicit reprocess
    EXPIRED: {QUEUED},
}


class InvalidTransition(Exception):
    pass


class StopPipeline(Exception):
    """Raised by a step to end the pipeline early in a non-processed status.

    This is a decision, not an error: detection needs a human (REVIEW) or the
    episode is dropped on purpose (SKIPPED). The worker moves the episode to
    `status` with `detail` as its status_detail and never auto-retries it.
    `job_error` records the job as failed with that text so the reason shows
    in the job history, otherwise the job counts as a success.
    """

    def __init__(self, status: str, detail: str, *, job_error: str | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.job_error = job_error


def validate_transition(current: str, new: str) -> None:
    if new not in _TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"cannot transition episode from {current!r} to {new!r}")
