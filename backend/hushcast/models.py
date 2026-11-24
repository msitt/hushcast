"""ORM models. Statuses/steps are plain strings validated by pipeline/state.py."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite stores/returns naive datetimes, everything here is UTC, so attach
    tzinfo on load. API responses then serialize with an offset (+00:00) and
    browsers render them in the viewer's local time zone."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    poll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # consecutive failed polls, reset on the next success. Drives the
    # feed_poll_failing notification (a single blip shouldn't page anyone).
    consecutive_poll_failures: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    detection_hints: Mapped[str | None] = mapped_column(Text, nullable=True)  # user-written, never touched by AI
    learned_hints: Mapped[str | None] = mapped_column(Text, nullable=True)  # distiller-owned
    hints_distilled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    episodes: Mapped[list[Episode]] = relationship(back_populates="feed", cascade="all, delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uq_episode_feed_guid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    description_html: Mapped[str] = mapped_column(Text, default="")
    source_enclosure_url: Mapped[str] = mapped_column(Text)
    source_enclosure_type: Mapped[str] = mapped_column(Text, default="audio/mpeg")
    source_enclosure_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="discovered", index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # Step name of the most recent failure. retry_count tracks consecutive
    # failures of this same step.
    last_failed_step: Mapped[str | None] = mapped_column(String(20), nullable=True)

    original_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ad_seconds_removed: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    feed: Mapped[Feed] = relationship(back_populates="episodes")
    jobs: Mapped[list[Job]] = relationship(back_populates="episode", cascade="all, delete-orphan")
    segments: Mapped[list[Segment]] = relationship(back_populates="episode", cascade="all, delete-orphan")
    transcript: Mapped[Transcript | None] = relationship(back_populates="episode", cascade="all, delete-orphan", uselist=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    step: Mapped[str] = mapped_column(String(20))  # download|transcribe|detect|cut|finalize
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|success|failed|skipped
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_text: Mapped[str] = mapped_column(Text, default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")

    episode: Mapped[Episode] = relationship(back_populates="jobs")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(Text, default="openai_compat")
    model: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_path: Mapped[str] = mapped_column(Text)  # normalized Transcript JSON on disk
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    episode: Mapped[Episode] = relationship(back_populates="transcript")


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(20))  # ad|sponsor|self_promo
    reason: Mapped[str] = mapped_column(Text, default="")
    kept: Mapped[bool] = mapped_column(Boolean, default=False)  # user says: not an ad (false positive)
    # llm | manual (user-added false negative) | promo (promo-only episode, informational, never cut)
    # | optional (eval-only: cutting or keeping this range is fine either way. Always kept=True,
    # never cut and never a correction)
    source: Mapped[str] = mapped_column(String(10), default="llm")
    # Transcript excerpt snapshotted at correction time (range ±context), makes
    # corrections self-contained for the hint distiller.
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    episode: Mapped[Episode] = relationship(back_populates="segments")


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)  # the full messages array, JSON-encoded
    response_text: Mapped[str] = mapped_column(Text)
    # request body minus messages (temperature, max_tokens, response_format, ...)
    # as finally sent, after any response_format fallback downgrades
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # complete raw provider payload (provider, reasoning, usage details) or,
    # for failed calls, the error body text
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # notable things that happened during the call, JSON list of strings
    events_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TranscriptionCall(Base):
    """One provider transcription request, usage accounting only (the raw
    payload debug log lives on disk next to the transcript)."""

    __tablename__ = "transcription_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    part: Mapped[int] = mapped_column(Integer, default=1)
    parts: Mapped[int] = mapped_column(Integer, default=1)
    audio_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    upload_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)


class EvalLabel(Base):
    """A human verdict on an episode for the detection eval set.

    verified: the stored truth spans are the complete, correct ad ranges. The
    row snapshots everything a detection re-run needs (transcript, cues,
    duration) because the episode's own files are retention-swept and
    its segments are wiped by re-detect. Hints are not snapshotted, evals run
    with the current ones.
    excluded: kept out of the eval set (e.g. a bad transcript), note says why.
    """

    __tablename__ = "eval_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), unique=True, index=True
    )
    verdict: Mapped[str] = mapped_column(String(20))  # verified | excluded
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # snapshot, verified labels only
    truth_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # [{start, end, category}]
    promo: Mapped[bool] = mapped_column(Boolean, default=False)  # truth is "promo-only episode"
    # [[start, end]] ranges scoring ignores: cutting or keeping them is fine either way
    optional_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # file | llm_prompt
    cues_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    podcast_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    episode_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # earliest correction on the episode at snapshot time, to tell whether the
    # current learned hints may have been distilled from this episode
    first_corrected_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # hash of the transcript and cues snapshot: a run's result for this episode
    # is only comparable while the inputs it ran on are still the label's
    inputs_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class EvalModel(Base):
    """A model to run evals with. base_url/api_key override the eval connection
    (settings eval_llm_*, falling back to the production llm_* connection)."""

    __tablename__ = "eval_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    model: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # secret, never returned by the API
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # added to the request body
    # manual USD prices per million tokens, used only when the provider reports no cost
    input_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_mtok: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EvalRun(Base):
    """One model run over the verified eval set."""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("eval_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # snapshots, so a run stays readable after its model entry changes or is deleted
    name: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, default="{}")  # endpoint, temperature, extra params, pricing
    hints_mode: Mapped[str] = mapped_column(String(10), default="current")  # current | none
    repeats: Mapped[int] = mapped_column(Integer, default=3)
    total: Mapped[int] = mapped_column(Integer, default=0)  # episode detections planned
    # hash of the hints and detection settings the run used: runs with
    # different fingerprints were not run under the same conditions
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # queued | running | done | cancelled | interrupted | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class EvalResult(Base):
    """One detection of one episode within a run. Kept apart from llm_calls on
    purpose: eval calls must not show up as an episode's detection history."""

    __tablename__ = "eval_results"
    __table_args__ = (UniqueConstraint("run_id", "episode_id", "repeat", name="uq_eval_result"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    repeat: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(10))  # cut | promo | review | error
    segments_json: Mapped[str] = mapped_column(Text, default="[]")  # [[start, end, category]]
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # detections it took: 2 when the first tripped a retryable check (overlong segment)
    attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    calls: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = unknown, 0 = free
    llm_elapsed_s: Mapped[float] = mapped_column(Float, default=0.0)
    inputs_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # the label inputs it ran on
    responses_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # raw LLM replies, for debugging
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
