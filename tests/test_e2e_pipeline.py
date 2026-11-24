"""End-to-end pipeline test against stub transcription/LLM/podcast-CDN servers.

Requires ffmpeg. Exercises: add feed -> initial poll -> download -> transcribe
-> detect -> cut -> finalize -> served RSS + byte-range audio.
"""
import re
import shutil
import socket
import subprocess
import threading
import time
from xml.etree import ElementTree as ET

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_stub_app(audio_path) -> FastAPI:
    app = FastAPI()
    port_holder: dict = {}
    app.state.port_holder = port_holder

    @app.get("/feed.xml")
    def feed():
        port = port_holder["port"]
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Stub Show</title><link>http://example.com</link><description>d</description>
    <item>
      <title>Stub Episode 1</title>
      <guid isPermaLink="false">stub-ep-1</guid>
      <pubDate>Wed, 20 Aug 2025 10:00:00 GMT</pubDate>
      <enclosure url="http://127.0.0.1:{port}/ep1.mp3" length="0" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""
        return Response(content=xml, media_type="application/rss+xml")

    @app.get("/ep1.mp3")
    def audio():
        return FileResponse(audio_path, media_type="audio/mpeg")

    @app.get("/healthcheck")
    def health():
        return {"status": "ok"}

    @app.post("/v1/audio/transcriptions")
    def transcribe():
        segments = [
            {"id": i, "start": i * 5.0, "end": (i + 1) * 5.0, "text": f"spoken line {i}"}
            for i in range(12)
        ]
        return {"language": "en", "duration": 60.0, "segments": segments, "usage": {"cost": 0.0125}}

    # "ads" = one 10s sponsor spot, "overcut" = flags most of the episode without
    # the promo flag (a misfire), "promo" = flags it all and says the episode is promo-only
    app.state.llm_mode = "ads"

    @app.post("/v1/chat/completions")
    async def chat(body: dict):
        system = body["messages"][0]["content"]
        if "distill" in system.lower() or "corrections" in system.lower():
            content = (
                '{"feed_hints": ["the host reads sponsor spots with a promo code"], '
                '"global_hints": ["segments with promo codes are sponsorships"]}'
            )
        elif app.state.llm_mode == "overcut":
            content = (
                '{"segments": [{"start": 0.0, "end": 55.0, "category": "ad", '
                '"reason": "everything"}], "whole_episode_promo": false}'
            )
        elif app.state.llm_mode == "promo":
            content = (
                '{"segments": [{"start": 0.0, "end": 55.0, "category": "self_promo", '
                '"reason": "trailer for another show"}], "whole_episode_promo": true}'
            )
        else:
            content = (
                '{"segments": [{"start": 10.0, "end": 20.0, "category": "sponsor", '
                '"reason": "promo code read"}]}'
            )
        resp = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        if body.get("model") == "stub-free":
            # a provider that reports cost, and the model is free
            resp["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0}
        return resp

    return app


STUB_APPS: dict[str, FastAPI] = {}


@pytest.fixture(scope="module")
def stub_server(tmp_path_factory):
    audio = tmp_path_factory.mktemp("stub") / "ep1.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
         "-c:a", "libmp3lame", "-b:a", "128k", str(audio)],
        check=True, capture_output=True,
    )
    port = _free_port()
    app = make_stub_app(audio)
    app.state.port_holder["port"] = port
    STUB_APPS[f"http://127.0.0.1:{port}"] = app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("stub server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_full_pipeline(stub_server, tmp_path_factory, monkeypatch):
    data_dir = tmp_path_factory.mktemp("hushcast-data")
    config_dir = tmp_path_factory.mktemp("hushcast-config")
    monkeypatch.setenv("HUSHCAST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUSHCAST_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("HUSHCAST_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("HUSHCAST_AUTH", "disabled")

    from hushcast import settings_store
    from hushcast.config import get_config

    get_config.cache_clear()
    settings_store.invalidate_cache()
    from hushcast.main import app

    with TestClient(app) as client:
        r = client.put("/api/settings", json={
            "transcription_base_url": f"{stub_server}/v1",
            "llm_base_url": f"{stub_server}/v1",
            "llm_model": "stub-model",
        })
        assert r.status_code == 200, r.text

        r = client.post("/api/feeds", json={"url": f"{stub_server}/feed.xml"})
        assert r.status_code == 201, r.text
        feed = r.json()

        # pre-existing episodes are never auto-queued, process the first one manually
        eps = client.get(f"/api/feeds/{feed['id']}/episodes").json()["items"]
        assert eps and eps[0]["status"] == "skipped", eps
        r = client.post(f"/api/episodes/{eps[0]['id']}/process")
        assert r.status_code == 200, r.text

        # wait for the full pipeline
        deadline = time.time() + 90
        episode = None
        while time.time() < deadline:
            eps = client.get(f"/api/feeds/{feed['id']}/episodes").json()["items"]
            if eps:
                episode = eps[0]
                if episode["status"] in ("processed", "failed"):
                    break
            time.sleep(0.5)
        assert episode is not None, "episode never appeared"
        if episode["status"] == "failed":
            detail = client.get(f"/api/episodes/{episode['id']}").json()
            pytest.fail(f"pipeline failed: {episode['status_detail']}\njobs: {detail['jobs']}")

        # 10s sponsor cut from 60s -> ~50s output
        assert episode["ad_seconds_removed"] == pytest.approx(10.0, abs=1.0)
        assert episode["processed_duration_s"] == pytest.approx(50.0, abs=2.0)

        # the cost the transcription provider reported shows up in the usage stats
        usage = client.get("/api/system/stats").json()["transcription"]
        assert usage["total"]["cost_usd"] == pytest.approx(0.0125 * usage["total"]["calls"])
        assert usage["by_model"][0]["cost_usd"] == pytest.approx(usage["total"]["cost_usd"])

        detail = client.get(f"/api/episodes/{episode['id']}").json()
        assert len(detail["segments"]) == 1
        seg = detail["segments"][0]
        assert (seg["start_s"], seg["end_s"], seg["category"]) == (10.0, 20.0, "sponsor")
        assert [j["step"] for j in detail["jobs"]] == ["download", "transcribe", "cues", "detect", "cut", "finalize"]
        assert all(j["status"] == "success" for j in detail["jobs"])

        # LLM call audit trail
        calls = client.get(f"/api/episodes/{episode['id']}/llm-calls").json()
        assert len(calls) == 1
        assert calls[0]["model"] == "stub-model"
        call = client.get(f"/api/llm-calls/{calls[0]['id']}").json()
        assert call["messages"][0]["role"] == "system"
        assert "spoken line" in call["messages"][1]["content"]
        assert "sponsor" in call["response"]

        # a range fully inside the detected 10-20s segment is rejected as "missed ad"...
        r = client.post(f"/api/episodes/{episode['id']}/segments", json={"start_s": 12.0, "end_s": 18.0})
        assert r.status_code == 409
        # ...but can be marked not-an-ad (boundary trim: a false-positive correction)
        r = client.post(
            f"/api/episodes/{episode['id']}/segments",
            json={"start_s": 10.0, "end_s": 13.0, "not_ad": True},
        )
        assert r.status_code == 201
        trim = r.json()
        assert trim["kept"] is True and trim["source"] == "manual"
        assert trim["category"] == "sponsor"  # inherited from the covering block
        # a not-an-ad marker outside any detected block is meaningless
        r = client.post(
            f"/api/episodes/{episode['id']}/segments",
            json={"start_s": 50.0, "end_s": 55.0, "not_ad": True},
        )
        assert r.status_code == 409
        # remove the trim so later correction-count assertions stay simple
        assert client.delete(f"/api/segments/{trim['id']}").status_code == 204

        # corrections: mark the detected segment as a false positive
        seg_id = detail["segments"][0]["id"]
        r = client.patch(f"/api/segments/{seg_id}", json={"kept": True})
        assert r.status_code == 200
        assert r.json()["kept"] is True
        assert r.json()["corrected_at"] is not None

        # and add a manual false-negative range
        r = client.post(f"/api/episodes/{episode['id']}/segments", json={"start_s": 30.0, "end_s": 45.0})
        assert r.status_code == 201
        manual_id = r.json()["id"]
        assert r.json()["source"] == "manual"

        # eval label: the corrected segments become the snapshotted truth
        ep_url = f"/api/episodes/{episode['id']}"
        r = client.put(f"{ep_url}/eval-label", json={"verdict": "verified"})
        assert r.status_code == 200, r.text
        label = r.json()
        assert label["truth"] == [{"start": 30.0, "end": 45.0, "category": "ad"}]
        assert label["transcript_source"] == "file"
        assert label["matches_segments"] is True
        listed = client.get(f"/api/feeds/{feed['id']}/episodes").json()["items"]
        assert [e["eval_verdict"] for e in listed] == ["verified"]
        # the eval set overview: one verified label, the feed is covered, nothing left to label
        overview = client.get("/api/evals/labels").json()
        assert (overview["verified"], overview["excluded"]) == (1, 0)
        assert overview["verified_s"] == pytest.approx(60.0, abs=1.0)
        assert [(f["verified"], f["candidates"]) for f in overview["feeds"]] == [(1, 0)]
        row = overview["labels"][0]
        assert (row["ad_ranges"], row["ad_s"], row["matches_segments"], row["hints_leak"]) == (1, 15.0, True, False)
        assert client.get(ep_url).json()["next_unlabeled_id"] is None  # the feed's only episode
        # undoing a correction leaves the snapshot alone but flags it as out of date
        assert client.patch(f"/api/segments/{seg_id}", json={"kept": False}).status_code == 200
        assert client.get(ep_url).json()["eval_label"]["matches_segments"] is False
        assert client.patch(f"/api/segments/{seg_id}", json={"kept": True}).status_code == 200
        assert client.get(ep_url).json()["eval_label"]["matches_segments"] is True

        # with the transcript file gone (retention), it is rebuilt from the detection prompt
        transcript_file = data_dir / "transcripts" / f"{episode['id']}.json"
        stash = transcript_file.with_suffix(".stash")
        transcript_file.rename(stash)
        try:
            assert client.get(ep_url).json()["transcript_source"] == "llm_prompt"
            rebuilt = client.get(f"{ep_url}/transcript").json()
            assert [s["text"] for s in rebuilt["segments"]] == [f"spoken line {i}" for i in range(12)]
            r = client.put(f"{ep_url}/eval-label", json={"verdict": "verified"})
            assert r.status_code == 200 and r.json()["transcript_source"] == "llm_prompt"
        finally:
            stash.rename(transcript_file)

        export = client.get("/api/evals/export").json()
        assert export["excluded"] == 0 and len(export["episodes"]) == 1
        exported = export["episodes"][0]
        assert exported["truth"] == label["truth"] and exported["duration_s"] == pytest.approx(60.0, abs=1.0)
        assert len(exported["transcript"]["segments"]) == 12
        assert "detection_prompt" in export["settings"] and "llm_api_key" not in export["settings"]
        assert exported["hints_leak"] is False  # nothing has been distilled yet

        r = client.put(f"{ep_url}/eval-label", json={"verdict": "excluded", "note": "bad transcript"})
        assert r.json()["verdict"] == "excluded" and r.json()["truth"] is None
        assert client.get("/api/evals/summary").json() == {"verified": 0, "excluded": 1}
        assert client.get("/api/evals/export").json()["episodes"] == []
        assert client.delete(f"{ep_url}/eval-label").status_code == 204
        assert client.get(ep_url).json()["eval_label"] is None

        # feed now reports new corrections
        feed_now = client.get(f"/api/feeds/{feed['id']}").json()
        assert feed_now["new_corrections"] == 2

        # distill returns a proposal without saving
        r = client.post(f"/api/feeds/{feed['id']}/distill")
        assert r.status_code == 200, r.text
        proposal = r.json()
        assert "promo code" in proposal["feed_hints"]
        assert proposal["corrections_used"] == 2
        assert client.get(f"/api/feeds/{feed['id']}").json()["learned_hints"] is None

        # accept: feed hints via PATCH, global via settings
        r = client.patch(f"/api/feeds/{feed['id']}", json={"learned_hints": proposal["feed_hints"]})
        assert r.json()["learned_hints"] == proposal["feed_hints"]
        assert r.json()["new_corrections"] == 0  # distilled_at now covers them
        client.put("/api/settings", json={"global_learned_hints": proposal["global_hints"]})
        assert client.get("/api/settings").json()["global_learned_hints"] == proposal["global_hints"]

        # a label whose corrections were just distilled is flagged, and the
        # export carries the current hints for evals to run with
        assert client.put(f"{ep_url}/eval-label", json={"verdict": "verified"}).status_code == 200
        export = client.get("/api/evals/export").json()
        assert export["episodes"][0]["hints_leak"] is True
        assert export["feeds"][str(feed["id"])]["learned_hints"] == proposal["feed_hints"]
        assert export["global_learned_hints"] == proposal["global_hints"]

        # eval runs in the server: two models over the one verified episode
        calls_before = len(client.get(f"{ep_url}/llm-calls").json())
        r = client.post("/api/evals/models", json={"name": "Unknown cost", "model": "stub-model"})
        assert r.status_code == 201, r.text
        unknown_id = r.json()["id"]
        assert r.json()["effective_base_url"] == f"{stub_server}/v1" and r.json()["has_api_key"] is False
        assert client.post("/api/evals/models", json={"name": "unknown cost", "model": "x"}).status_code == 409
        free_id = client.post(
            "/api/evals/models",
            json={"name": "Free", "model": "stub-free", "api_key": "sk-eval", "extra_params": {"seed": 7}},
        ).json()["id"]
        listed = {m["name"]: m for m in client.get("/api/evals/models").json()}
        assert listed["Free"]["has_api_key"] is True and "api_key" not in listed["Free"]
        assert client.post(f"/api/evals/models/{free_id}/test").json()["ok"] is True
        estimate = client.get("/api/evals/runs/estimate", params={"repeats": 2}).json()
        assert (estimate["episodes"], estimate["detections"]) == (1, 2) and estimate["input_tokens"] > 0

        r = client.post("/api/evals/runs", json={"model_ids": [unknown_id, free_id], "repeats": 2})
        assert r.status_code == 201, r.text
        run_ids = [x["id"] for x in r.json()]
        deadline = time.time() + 30
        while time.time() < deadline:
            runs = {x["id"]: x for x in client.get("/api/evals/runs").json()}
            if all(runs[i]["status"] == "done" for i in run_ids):
                break
            time.sleep(0.2)
        assert [runs[i]["status"] for i in run_ids] == ["done", "done"], runs
        assert [(runs[i]["done"], runs[i]["total"], runs[i]["errors"]) for i in run_ids] == [(2, 2, 0)] * 2
        # both runs have a usable result for the one verified episode
        assert [(runs[i]["covered"], runs[i]["verified"]) for i in run_ids] == [(1, 1)] * 2
        # unknown cost stays unknown, a reported zero is a real cost
        assert runs[run_ids[0]]["cost_usd"] is None and runs[run_ids[1]]["cost_usd"] == 0.0
        # eval calls never show up as the episode's own detection history
        assert len(client.get(f"{ep_url}/llm-calls").json()) == calls_before

        res = client.get("/api/evals/results", params={"runs": ",".join(map(str, run_ids))}).json()
        assert [e["episode_id"] for e in res["episodes"]] == [episode["id"]]
        assert res["leaked"] == 1 and res["mixed_conditions"] is False
        scored = {x["name"]: x for x in res["runs"]}
        assert scored["Unknown cost"]["total_cost_usd"] is None and scored["Free"]["total_cost_usd"] == 0.0
        assert scored["Free"]["metrics"]["cost_per_episode_usd"]["mean"] == 0.0
        assert scored["Unknown cost"]["metrics"]["cost_per_episode_usd"]["mean"] is None
        # truth is 30-45s, the stub flags 10-20s: 10 s of content cut, the ad missed (15 s less the collar)
        cell = scored["Free"]["episodes"][str(episode["id"])]
        assert (cell["content_cut_s"], cell["missed_ad_s"], cell["weighted_s"]) == (10.0, 13.0, 43.0)
        assert [x["segments"] for x in cell["repeats"]] == [[[10.0, 20.0, "sponsor"]]] * 2
        heavier = client.get("/api/evals/results", params={"runs": str(run_ids[1]), "content_weight": 10}).json()
        assert heavier["runs"][0]["episodes"][str(episode["id"])]["weighted_s"] == 113.0

        # re-saving a label with unchanged inputs keeps results, a done run can't be cancelled or resumed
        assert client.put(f"{ep_url}/eval-label", json={"verdict": "verified"}).status_code == 200
        res = client.get("/api/evals/results", params={"runs": str(run_ids[0])}).json()
        assert res["runs"][0]["stale_episodes"] == 0 and len(res["episodes"]) == 1
        assert client.post(f"/api/evals/runs/{run_ids[0]}/cancel").status_code == 409
        assert client.post(f"/api/evals/runs/{run_ids[0]}/resume").status_code == 409

        # promoting only touches the model here: it has no endpoint of its own
        r = client.post(f"/api/evals/models/{free_id}/promote").json()
        assert r["changed"] == ["llm_model"] and r["extra_params_not_applied"] is True
        assert client.get("/api/settings").json()["llm_model"] == "stub-free"
        client.put("/api/settings", json={"llm_model": "stub-model"})

        assert client.delete(f"/api/evals/models/{unknown_id}").status_code == 204
        assert {x["id"]: x["model_id"] for x in client.get("/api/evals/runs").json()}[run_ids[0]] is None
        for i in run_ids:
            assert client.delete(f"/api/evals/runs/{i}").status_code == 204
        assert client.get("/api/evals/runs").json() == []
        assert client.delete(f"/api/evals/models/{free_id}").status_code == 204
        assert client.delete(f"{ep_url}/eval-label").status_code == 204

        # manual segment can be deleted, llm segment cannot
        assert client.delete(f"/api/segments/{manual_id}").status_code == 204
        assert client.delete(f"/api/segments/{seg_id}").status_code == 409

        # served RSS
        token = client.get("/api/settings").json()["feed_token"]
        r = client.get(f"/p/{token}/{feed['slug']}/feed.xml")
        assert r.status_code == 200
        root = ET.fromstring(r.content)
        item = root.find("channel/item")
        assert item.findtext("guid") == "stub-ep-1"
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url").replace("http://testserver", "")
        assert int(enclosure.get("length")) > 0

        # byte-range audio (what podcast clients do)
        r = client.get(audio_url, headers={"Range": "bytes=0-1023"})
        assert r.status_code == 206
        assert len(r.content) == 1024

        # wrong token is a 404
        r = client.get(f"/p/wrongtoken/{feed['slug']}/feed.xml")
        assert r.status_code == 404

        # original deleted (keep_originals=False), processed kept
        originals = list((data_dir / "audio" / "original").iterdir())
        assert originals == []
        assert (data_dir / "audio" / "processed" / f"{episode['id']}.mp3").exists()

        def wait_for(ep_id: int, statuses: tuple[str, ...]) -> dict:
            deadline = time.time() + 90
            while time.time() < deadline:
                ep = client.get(f"/api/episodes/{ep_id}").json()
                if ep["status"] in statuses:
                    return ep
                time.sleep(0.5)
            pytest.fail(f"episode {ep_id} never reached {statuses}, last: {ep['status']} {ep['status_detail']}")

        stub = STUB_APPS[stub_server]
        ep_id = episode["id"]

        # --- over-cut without the promo flag: the circuit breaker parks the episode for review,
        # segments stay visible, nothing is cut, and it is not auto-retried as a failure
        stub.state.llm_mode = "overcut"
        assert client.post(f"/api/episodes/{ep_id}/reprocess", params={"from_step": "detect"}).status_code == 200
        ep = wait_for(ep_id, ("review", "processed", "failed"))
        assert ep["status"] == "review", ep
        # the exact figure depends on the duration ffprobe reports, which varies by ffmpeg build
        m = re.search(r"would remove (\d+\.\d)%", ep["status_detail"])
        assert m and float(m.group(1)) == pytest.approx(91.7, abs=0.5), ep["status_detail"]
        assert ep["retry_count"] == 0
        assert len(ep["segments"]) == 1 and ep["segments"][0]["kept"] is False
        assert ep["jobs"][-1]["step"] == "detect" and ep["jobs"][-1]["status"] == "failed"
        assert any(a["kind"] == "review_episodes" for a in client.get("/api/system/alerts").json())
        # the human trims the block (not-an-ad inside it) and cuts with what is left
        r = client.post(f"/api/episodes/{ep_id}/segments", json={"start_s": 0.0, "end_s": 40.0, "not_ad": True})
        assert r.status_code == 201
        assert client.post(f"/api/episodes/{ep_id}/reprocess", params={"from_step": "cut"}).status_code == 200
        ep = wait_for(ep_id, ("processed", "failed", "review"))
        assert ep["status"] == "processed", ep
        assert ep["ad_seconds_removed"] == pytest.approx(55.0, abs=1.0)  # cut ignores the kept trim's overlap
        assert [j["step"] for j in ep["jobs"][-2:]] == ["cut", "finalize"]

        # --- promo-only episode, default action: left out of the feed as skipped
        stub.state.llm_mode = "promo"
        assert client.post(f"/api/episodes/{ep_id}/reprocess", params={"from_step": "detect"}).status_code == 200
        ep = wait_for(ep_id, ("skipped", "processed", "failed", "review"))
        assert ep["status"] == "skipped", ep
        assert "promo-only episode" in ep["status_detail"]
        assert ep["jobs"][-1]["step"] == "detect" and ep["jobs"][-1]["status"] == "success"
        assert all(s["kept"] and s["source"] == "promo" for s in ep["segments"])
        assert client.patch(f"/api/segments/{ep['segments'][0]['id']}", json={"kept": False}).status_code == 409
        r = client.get(f"/p/{token}/{feed['slug']}/feed.xml")
        assert ET.fromstring(r.content).find("channel/item") is None

        # --- promo-only episode, passthrough: served untouched
        client.put("/api/settings", json={"promo_episode_action": "passthrough"})
        assert client.post(f"/api/episodes/{ep_id}/reprocess", params={"from_step": "detect"}).status_code == 200
        ep = wait_for(ep_id, ("processed", "skipped", "failed", "review"))
        assert ep["status"] == "processed", ep
        assert ep["ad_seconds_removed"] == 0.0
        assert ep["processed_duration_s"] == pytest.approx(60.0, abs=2.0)
        assert all(s["kept"] for s in ep["segments"])
        r = client.put("/api/settings", json={"promo_episode_action": "nonsense"})
        assert r.status_code == 400
