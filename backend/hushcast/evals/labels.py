"""Eval labels: snapshotting an episode's detection inputs and truth, and the dataset export."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__, settings_store
from ..config import get_config
from ..cues.base import Cue, cues_from_json, cues_to_json
from ..models import Episode, EvalLabel, Feed, LlmCall, Segment
from ..transcription.base import Transcript
from . import reconstruct
from .truth import SegmentRow, Truth, effective_truth

DATASET_VERSION = 1

# The non-secret settings that shape detection and post-processing. The eval
# runner starts from these so a run matches production unless a variant
# overrides something.
DETECTION_SETTINGS = (
    "detection_prompt",
    "llm_model",
    "llm_temperature",
    "llm_context_budget_tokens",
    "llm_max_tokens",
    "snap_tolerance_s",
    "min_duration_s",
    "merge_gap_s",
    "refine_window_s",
    "refine_min_gap_s",
    "cue_prompt_annotations",
    "cue_min_prompt_duration_s",
    "cue_bridge_max_gap_s",
    "cue_edge_max_extension_s",
)


class LabelError(Exception):
    """The episode can't be labeled as asked, the message says why."""


@dataclass
class DetectionInputs:
    transcript: Transcript
    transcript_source: str  # file | llm_prompt
    cues: list[Cue] | None


async def _detection_user_contents(session: AsyncSession, episode_id: int) -> list[str]:
    rows = (
        await session.execute(
            select(LlmCall.request_json).where(LlmCall.episode_id == episode_id).order_by(LlmCall.id)
        )
    ).scalars().all()
    out = []
    for raw in rows:
        try:
            messages = json.loads(raw)
        except ValueError:
            continue
        if isinstance(messages, list) and reconstruct.is_detection_prompt(messages):
            out.append(str(messages[1]["content"]))
    return out


def _load_json(path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


async def detection_inputs(session: AsyncSession, ep: Episode) -> DetectionInputs | None:
    """The transcript and cues detection last ran on.

    Prefers the files on disk (word timestamps, every cue). When retention has
    removed them, falls back to rebuilding from the stored detection prompt.
    """
    config = get_config()
    data = _load_json(config.transcripts_dir / f"{ep.id}.json")
    if data is not None:
        try:
            transcript = Transcript.from_json_dict(data)
        except (KeyError, TypeError, ValueError):
            transcript = None
        if transcript is not None:
            cue_data = _load_json(config.cues_dir / f"{ep.id}.json")
            try:
                cues = cues_from_json(cue_data) if isinstance(cue_data, list) else None
            except (KeyError, TypeError, ValueError):
                cues = None
            return DetectionInputs(transcript, "file", cues)
    contents = await _detection_user_contents(session, ep.id)
    rebuilt = reconstruct.rebuild(contents, ep.duration_s) if contents else None
    if rebuilt is None:
        return None
    return DetectionInputs(rebuilt.transcript, "llm_prompt", rebuilt.cues or None)


async def _segments(session: AsyncSession, episode_id: int) -> list[Segment]:
    return list((await session.execute(select(Segment).where(Segment.episode_id == episode_id))).scalars().all())


def _truth_of(rows: list[Segment]) -> Truth:
    return effective_truth([SegmentRow(r.start_s, r.end_s, r.category, r.kept, r.source) for r in rows])


async def current_truth(session: AsyncSession, episode_id: int) -> Truth:
    return _truth_of(await _segments(session, episode_id))


def truth_to_json(truth: Truth) -> str:
    return json.dumps([asdict(s) for s in truth.spans])


def optional_to_json(truth: Truth) -> str:
    return json.dumps([list(o) for o in truth.optional])


def label_optional(label: EvalLabel) -> list[list[float]]:
    return json.loads(label.optional_json) if label.optional_json else []


def truth_matches(label: EvalLabel, truth: Truth) -> bool:
    """Whether a verified label still agrees with the episode's segments."""
    if label.verdict != "verified" or label.truth_json is None:
        return True
    return (
        label.promo == truth.promo
        and json.loads(label.truth_json) == json.loads(truth_to_json(truth))
        and label_optional(label) == json.loads(optional_to_json(truth))
    )


async def save_label(
    session: AsyncSession, ep: Episode, verdict: str, note: str | None
) -> EvalLabel:
    inputs = None
    if verdict == "verified":
        inputs = await detection_inputs(session, ep)
        if inputs is None:
            raise LabelError(
                "no transcript to snapshot: the transcript file is gone and no detection prompt is stored"
            )
    label = (
        await session.execute(select(EvalLabel).where(EvalLabel.episode_id == ep.id))
    ).scalar_one_or_none()
    if label is None:
        label = EvalLabel(episode_id=ep.id, verdict=verdict)
        session.add(label)
    label.verdict = verdict
    label.note = (note or "").strip() or None
    # an excluded episode keeps no snapshot: it isn't part of the dataset
    for attr in (
        "truth_json", "optional_json", "transcript_json", "transcript_source", "cues_json", "duration_s",
        "podcast_title", "episode_title", "first_corrected_at", "inputs_hash",
    ):
        setattr(label, attr, None)
    label.promo = False
    label.updated_at = datetime.now(UTC)
    if inputs is None:  # excluded
        return label

    feed = await session.get(Feed, ep.feed_id)
    rows = await _segments(session, ep.id)
    truth = _truth_of(rows)
    corrected = [r.corrected_at for r in rows if r.corrected_at is not None]
    # Kept because re-detect deletes the segments (and their timestamps) while
    # the corrections may already be distilled into the hints.
    label.first_corrected_at = min(corrected) if corrected else None
    label.truth_json = truth_to_json(truth)
    label.optional_json = optional_to_json(truth) if truth.optional else None
    label.promo = truth.promo
    label.transcript_json = json.dumps(inputs.transcript.to_json_dict())
    label.transcript_source = inputs.transcript_source
    label.cues_json = json.dumps(cues_to_json(inputs.cues)) if inputs.cues else None
    label.inputs_hash = hashlib.sha256(
        (label.transcript_json + "|" + (label.cues_json or "")).encode()
    ).hexdigest()
    label.duration_s = ep.duration_s or inputs.transcript.duration
    label.podcast_title = feed.title if feed else ""
    label.episode_title = ep.title
    return label


def label_out(label: EvalLabel, truth: Truth) -> dict[str, Any]:
    return {
        "verdict": label.verdict,
        "note": label.note,
        "promo": label.promo,
        "truth": json.loads(label.truth_json) if label.truth_json else None,
        "optional": label_optional(label),
        "transcript_source": label.transcript_source,
        "matches_segments": truth_matches(label, truth),
        "created_at": label.created_at,
        "updated_at": label.updated_at,
    }


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def hints_leak(label: EvalLabel, feed: Feed | None, global_hints: str | None) -> bool:
    """Whether this episode's corrections may have been distilled into the hints
    a run uses. Then the hints partly describe its answer and flatter the score.

    A feed's distill run produces both its learned hints and the global hints,
    from that feed's corrections only, so its hints_distilled_at covers both.
    """
    if feed is None or label.first_corrected_at is None or feed.hints_distilled_at is None:
        return False
    if not (feed.learned_hints or global_hints):
        return False
    return _aware(label.first_corrected_at) <= _aware(feed.hints_distilled_at)


async def export_dataset(session: AsyncSession) -> dict[str, Any]:
    settings = await settings_store.get_all(session)
    global_hints = settings.get("global_learned_hints") or None
    feeds = {f.id: f for f in (await session.execute(select(Feed))).scalars()}
    rows = (
        await session.execute(
            select(EvalLabel, Episode.feed_id, Episode.published_at)
            .join(Episode, EvalLabel.episode_id == Episode.id)
            .order_by(EvalLabel.episode_id)
        )
    ).all()
    episodes = []
    excluded = 0
    for label, feed_id, published_at in rows:
        if label.verdict != "verified":
            excluded += 1
            continue
        episodes.append({
            "episode_id": label.episode_id,
            "feed_id": feed_id,
            "published_at": published_at.isoformat() if published_at else None,
            "podcast_title": label.podcast_title,
            "episode_title": label.episode_title,
            "duration_s": label.duration_s,
            "promo": label.promo,
            "truth": json.loads(label.truth_json or "[]"),
            "optional": label_optional(label),
            "transcript_source": label.transcript_source,
            "transcript": json.loads(label.transcript_json or "{}"),
            "cues": json.loads(label.cues_json) if label.cues_json else None,
            "hints_leak": hints_leak(label, feeds.get(feed_id), global_hints),
            "inputs_hash": label.inputs_hash,
            "labeled_at": label.updated_at.isoformat(),
        })
    used_feeds = {e["feed_id"] for e in episodes}
    return {
        "version": DATASET_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(),
        "settings": {k: settings[k] for k in DETECTION_SETTINGS},
        # current hints, what production would detect with today
        "global_learned_hints": global_hints,
        "feeds": {
            str(fid): {
                "title": feeds[fid].title,
                "detection_hints": feeds[fid].detection_hints or None,
                "learned_hints": feeds[fid].learned_hints or None,
            }
            for fid in sorted(used_feeds)
            if fid in feeds
        },
        "excluded": excluded,
        "episodes": episodes,
    }
