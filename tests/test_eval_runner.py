import json

from hushcast.detection import llm as llm_mod
from hushcast.evals import runner, summarize
from hushcast.evals.scoring import ScoreConfig


def _dataset():
    segments = [{"text": f"line {i}", "start": i * 10.0, "end": i * 10.0 + 9.5, "words": []} for i in range(60)]
    return {
        "version": 1,
        "settings": {"llm_model": "prod-model", "detection_prompt": "find ads"},
        "episodes": [
            {
                "episode_id": 7,
                "podcast_title": "Pod",
                "episode_title": "Ep",
                "duration_s": 600.0,
                "promo": False,
                "truth": [{"start": 100.0, "end": 159.5, "category": "ad"}],
                "transcript_source": "llm_prompt",
                "transcript": {"language": None, "duration": 600.0, "segments": segments},
                "cues": None,
                "feed_id": 3,
                "hints_leak": True,
            }
        ],
        "global_learned_hints": "- ads follow music",
        "feeds": {"3": {"title": "Pod", "detection_hints": "host reads ads", "learned_hints": None}},
    }


def _variant(tmp_path):
    path = tmp_path / "v.toml"
    path.write_text(
        'name = "fake"\n[settings]\nllm_base_url = "http://llm.invalid/v1"\nllm_model = "fake-1"\n'
        "[extra_params]\nreasoning_effort = \"low\"\n"
        "[pricing]\ninput_per_mtok = 1.0\noutput_per_mtok = 2.0\n",
        encoding="utf-8",
    )
    return runner.Variant.load(path)


async def test_run_and_summarize(tmp_path, monkeypatch):
    seen = []

    async def fake_chat(self, messages, *, schema=None, recorder=None):
        seen.append((self.model, self.extra_params, messages))
        if recorder:
            await recorder({"prompt_tokens": 1000, "completion_tokens": 100, "elapsed_s": 1.5})
        # an ad that over-runs by 10 s into content
        return llm_mod.ChatResult(
            content=json.dumps({"segments": [{"start": 100.0, "end": 169.5, "category": "ad", "reason": "x"}],
                                "whole_episode_promo": False}),
            finish_reason="stop",
        )

    monkeypatch.setattr(llm_mod.LLMClient, "chat", fake_chat)
    variant = _variant(tmp_path)
    cache = tmp_path / "cache"
    report = await runner.run_variant(
        _dataset(), variant, repeats=2, concurrency=2, hints_mode="current", cache_dir=cache
    )
    assert len(seen) == 2  # one call per repeat, repeats are distinct samples
    model, extra, messages = seen[0]
    assert model == "fake-1" and extra == {"reasoning_effort": "low"}
    assert messages[0]["content"] == "find ads"
    assert "host reads ads" in messages[1]["content"]
    assert "ads follow music" in messages[1]["content"]
    assert report["episodes"]["7"]["hints_leak"] is True
    assert summarize.leaked_episodes([report], ["7"]) == {"7"}
    assert report["effective_settings"]["llm_model"] == "fake-1"
    run = report["runs"][0]
    assert run["outcome"] == "cut"
    assert run["segments"] == [[100.0, 169.5, "ad"]]
    assert run["cost_usd"] == (1000 * 1.0 + 100 * 2.0) / 1e6

    # same requests again: all served from the cache
    again = await runner.run_variant(
        _dataset(), variant, repeats=2, concurrency=2, hints_mode="current", cache_dir=cache
    )
    assert len(seen) == 2
    assert all(r["cached_calls"] == r["calls"] == 1 for r in again["runs"])
    assert again["runs"][0]["prompt_tokens"] == 1000

    s = summarize.summarize_report(report, ["7"], ScoreConfig())
    assert s["repeats"] == 2
    # 10 s over-run minus the 1 s collar
    assert abs(s["metrics"]["content_cut_per_h"]["mean"] - 9 * 6) < 1e-6
    assert s["metrics"]["missed_ad_per_h"]["mean"] == 0
    assert "weighted error" in summarize.render_table([s])
    assert "7 Ep" in summarize.render_per_episode([report], ["7"], ScoreConfig())


async def test_hints_none_and_failed_episode(tmp_path, monkeypatch):
    async def failing_chat(self, messages, *, schema=None, recorder=None):
        assert "host reads ads" not in messages[1]["content"]
        assert "ads follow music" not in messages[1]["content"]
        return llm_mod.ChatResult(content="not json at all", finish_reason="stop")

    monkeypatch.setattr(llm_mod.LLMClient, "chat", failing_chat)
    report = await runner.run_variant(
        _dataset(), _variant(tmp_path), repeats=1, concurrency=1, hints_mode="none", cache_dir=tmp_path / "c"
    )
    assert report["episodes"]["7"]["hints_leak"] is False  # no hints, nothing to leak
    run = report["runs"][0]
    assert run["outcome"] == "error"
    assert "malformed" in run["error"]
    assert run["calls"] == 1 + llm_mod.MALFORMED_RETRIES
    assert run["retries"] == llm_mod.MALFORMED_RETRIES


def test_variant_rejects_inline_api_key(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('[settings]\nllm_api_key = "sk-123"\n', encoding="utf-8")
    try:
        runner.Variant.load(path)
    except ValueError as exc:
        assert "api_key_env" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_variant_must_name_model_and_endpoint(tmp_path):
    path = tmp_path / "v.toml"
    path.write_text("[settings]\nllm_temperature = 0.2\n", encoding="utf-8")
    try:
        runner.Variant.load(path)
    except ValueError as exc:
        assert "llm_base_url and llm_model" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_summarize_defaults_to_newest_report_per_variant(tmp_path, monkeypatch, capsys):
    from hushcast.evals import __main__ as cli

    monkeypatch.chdir(tmp_path)
    reports = tmp_path / ".evals" / "reports"
    reports.mkdir(parents=True)

    def write(name, variant, created, end):
        report = {
            "created_at": created,
            "variant": {"name": variant},
            "effective_settings": {"llm_model": variant},
            "hints_mode": "current",
            "repeats": 1,
            "episodes": {"7": {"episode_title": "Ep", "duration_s": 600.0, "promo": False,
                               "truth": [[100.0, 160.0, "ad"]], "hints_leak": False}},
            "runs": [{"episode_id": 7, "repeat": 0, "outcome": "cut", "segments": [[100.0, end, "ad"]],
                      "calls": 1, "retries": 0, "llm_elapsed_s": 1.0, "cost_usd": None}],
        }
        (reports / name).write_text(json.dumps(report), encoding="utf-8")

    write("a-old.json", "model-a", "2026-11-01T00:00:00+00:00", 200.0)
    write("a-new.json", "model-a", "2026-11-02T00:00:00+00:00", 160.0)
    write("b.json", "model-b", "2026-11-01T12:00:00+00:00", 170.0)

    assert cli.main(["summarize"]) == 0
    out = capsys.readouterr().out
    assert "a-new.json" in out and "b.json" in out and "a-old.json" not in out

    # explicit globs work without shell expansion, and same-named reports get distinct columns
    assert cli.main(["summarize", str(reports / "a-*.json")]) == 0
    out = capsys.readouterr().out
    assert "model-a @11-01 00:00" in out and "model-a @11-02 00:00" in out

    assert cli.main(["summarize", str(reports / "missing.json")]) == 1
    assert "no such report" in capsys.readouterr().err
