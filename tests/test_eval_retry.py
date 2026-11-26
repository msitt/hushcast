"""Retrying a finished eval run: which detections get redone, and that nothing
is deleted when the run can't continue under its original conditions."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from hushcast import db, settings_store
from hushcast.api.evals import EvalRetryIn, retry_eval_run
from hushcast.config import get_config
from hushcast.evals import jobs, labels
from hushcast.models import Episode, EvalLabel, EvalModel, EvalResult, EvalRun, Feed


@pytest.fixture
async def app_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HUSHCAST_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HUSHCAST_DATA_DIR", str(tmp_path / "data"))
    get_config.cache_clear()
    config = get_config()
    config.ensure_dirs()
    await db.init_db()
    settings_store.invalidate_cache()
    yield config
    await db.dispose_db()
    settings_store.invalidate_cache()


async def _seed(session, *, episodes=(1, 2, 3)):
    """A done run over the verified episodes, 2 repeats each, all detections ok."""
    await settings_store.set_many(session, {"llm_base_url": "https://llm.example/v1"})
    feed = Feed(slug="show", source_url="https://feed.example/rss", title="Show")
    session.add(feed)
    await session.flush()
    for eid in episodes:
        session.add(Episode(id=eid, feed_id=feed.id, guid=f"g{eid}", source_enclosure_url="https://x/a.mp3"))
        session.add(EvalLabel(
            episode_id=eid, verdict="verified", truth_json="[]", transcript_json="{}",
            duration_s=600.0, inputs_hash=f"h{eid}",
        ))
    model = EvalModel(name="m", model="vendor/m")
    session.add(model)
    await session.flush()
    run = EvalRun(model_id=model.id, name="m", model="vendor/m", hints_mode="current", repeats=2, status="done")
    run.fingerprint = jobs.fingerprint(await labels.export_dataset(session), run.hints_mode)
    session.add(run)
    await session.flush()
    for eid in episodes:
        for r in range(2):
            session.add(EvalResult(run_id=run.id, episode_id=eid, repeat=r, outcome="cut", inputs_hash=f"h{eid}"))
    await session.commit()
    return run


async def _results(session, run_id):
    rows = await session.execute(select(EvalResult.episode_id, EvalResult.repeat).where(EvalResult.run_id == run_id))
    return sorted(rows.all())


async def test_retry_redoes_failed_and_stale_detections(app_db):
    async with db.session_factory()() as session:
        run = await _seed(session)
        failed = (await session.execute(
            select(EvalResult).where(EvalResult.episode_id == 1, EvalResult.repeat == 0)
        )).scalar_one()
        failed.outcome, failed.error = "error", "TypeError"
        (await session.get(EvalLabel, 2)).inputs_hash = "h2-changed"  # episode 2's results went stale
        await session.commit()

        preview = await retry_eval_run(run.id, EvalRetryIn(dry_run=True), session)
        assert preview == {"redone": 3, "new": 0}
        assert len(await _results(session, run.id)) == 6  # a dry run deletes nothing

        assert await retry_eval_run(run.id, EvalRetryIn(), session) == {"redone": 3, "new": 0}
        assert await _results(session, run.id) == [(1, 1), (3, 0), (3, 1)]
        assert (await session.get(EvalRun, run.id)).status == "queued"


async def test_retry_one_detection_counts_episodes_labeled_since(app_db):
    async with db.session_factory()() as session:
        run = await _seed(session)
        session.add(Episode(id=4, feed_id=1, guid="g4", source_enclosure_url="https://x/a.mp3"))
        session.add(EvalLabel(episode_id=4, verdict="verified", truth_json="[]", transcript_json="{}",
                              duration_s=600.0, inputs_hash="h4"))
        await session.commit()

        assert await retry_eval_run(run.id, EvalRetryIn(episode_id=3, repeat=1), session) == {"redone": 1, "new": 2}
        assert await _results(session, run.id) == [(1, 0), (1, 1), (2, 0), (2, 1), (3, 0)]


async def test_nothing_to_retry(app_db):
    async with db.session_factory()() as session:
        run = await _seed(session)
        with pytest.raises(HTTPException) as exc:
            await retry_eval_run(run.id, EvalRetryIn(), session)
        assert exc.value.status_code == 409


async def test_changed_conditions_refuse_before_deleting(app_db):
    async with db.session_factory()() as session:
        run = await _seed(session)
        (await session.execute(select(EvalResult).where(EvalResult.episode_id == 1))).scalars().first().outcome = "error"
        await settings_store.set_many(session, {"llm_max_tokens": 32768})
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await retry_eval_run(run.id, EvalRetryIn(), session)
        assert exc.value.status_code == 409 and "changed" in exc.value.detail
        assert len(await _results(session, run.id)) == 6


async def test_unknown_episode_is_refused(app_db):
    async with db.session_factory()() as session:
        run = await _seed(session)
        with pytest.raises(HTTPException) as exc:
            await retry_eval_run(run.id, EvalRetryIn(episode_id=99, repeat=0), session)
        assert "99" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await retry_eval_run(run.id, EvalRetryIn(episode_id=1, repeat=2), session)  # repeats are 0 and 1
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await retry_eval_run(run.id, EvalRetryIn(episode_id=1), session)
        assert exc.value.status_code == 400
