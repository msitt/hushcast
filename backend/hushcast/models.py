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
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    kept: Mapped[bool] = mapped_column(Boolean, default=False)  # user says: not an ad (false positive)
    source: Mapped[str] = mapped_column(String(10), default="llm")  # llm | manual (user-added false negative)
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
