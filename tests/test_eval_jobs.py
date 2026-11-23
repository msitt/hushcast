from types import SimpleNamespace

import pytest

from hushcast.detection.llm import reported_cost
from hushcast.evals import jobs
from hushcast.evals.runner import CallStats, Pricing

PROD = {"llm_base_url": "https://gateway.example/v1", "llm_api_key": "sk-prod",
        "eval_llm_base_url": "", "eval_llm_api_key": ""}


def model(base_url=None, api_key=None):
    return SimpleNamespace(base_url=base_url, api_key=api_key)


class TestResolveConnection:
    def test_defaults_to_production(self):
        assert jobs.resolve_connection(PROD, model()) == ("https://gateway.example/v1", "sk-prod")

    def test_eval_key_on_the_same_gateway(self):
        settings = {**PROD, "eval_llm_api_key": "sk-eval"}
        assert jobs.resolve_connection(settings, model()) == ("https://gateway.example/v1", "sk-eval")
        # naming the same endpoint again changes nothing
        settings["eval_llm_base_url"] = "https://gateway.example/v1/"
        assert jobs.resolve_connection(settings, model()) == ("https://gateway.example/v1", "sk-eval")

    def test_model_key_overrides_on_the_same_endpoint(self):
        settings = {**PROD, "eval_llm_api_key": "sk-eval"}
        assert jobs.resolve_connection(settings, model(api_key="sk-model"))[1] == "sk-model"

    def test_a_different_endpoint_never_inherits_a_key(self):
        # the production and eval keys must not be sent to another provider
        settings = {**PROD, "eval_llm_api_key": "sk-eval"}
        assert jobs.resolve_connection(settings, model(base_url="https://other.example/v1")) == (
            "https://other.example/v1", "",
        )
        assert jobs.resolve_connection(settings, model("https://other.example/v1", "sk-other")) == (
            "https://other.example/v1", "sk-other",
        )
        eval_elsewhere = {**PROD, "eval_llm_base_url": "https://evals.example/v1"}
        assert jobs.resolve_connection(eval_elsewhere, model()) == ("https://evals.example/v1", "")

    def test_no_endpoint_is_an_error(self):
        blank = {**PROD, "llm_base_url": ""}
        m = SimpleNamespace(base_url=None, api_key=None, model="m", temperature=None)
        with pytest.raises(jobs.RunConfigError):
            jobs.run_settings(blank, m)


class TestCost:
    def test_reported_cost_sources(self):
        assert reported_cost({"usage": {"cost": 0.0012}}, {}) == 0.0012
        assert reported_cost({"usage": {}}, {"x-litellm-response-cost": "0.004"}) == 0.004
        assert reported_cost({"usage": {"cost": 0}}, {}) == 0.0  # free is a real answer
        assert reported_cost({"usage": {"prompt_tokens": 5}}, {}) is None
        assert reported_cost({}, {"x-litellm-response-cost": "None"}) is None
        assert reported_cost({"usage": {"cost": -1}}, None) is None

    def test_zero_is_known_and_missing_is_unknown(self):
        free = CallStats()
        free.count({"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 0.0})
        assert free.cost(None) == 0.0

        unknown = CallStats()
        unknown.count({"prompt_tokens": 1_000_000, "completion_tokens": 100_000})
        assert unknown.cost(None) is None
        assert unknown.cost(Pricing(1.0, None)) is None  # half a price is no price
        assert unknown.cost(Pricing(1.0, 2.0)) == pytest.approx(1.2)

    def test_reported_cost_wins_over_manual_prices(self):
        stats = CallStats()
        stats.count({"prompt_tokens": 1_000_000, "completion_tokens": 0, "cost_usd": 0.5})
        assert stats.cost(Pricing(9.0, 9.0)) == 0.5

    def test_no_answered_calls_cost_nothing(self):
        assert CallStats().cost(None) == 0.0


def test_fingerprint_tracks_hints_only_when_used():
    base = {"settings": {"detection_prompt": "p", "merge_gap_s": 7.0}, "feeds": {"1": {"learned_hints": "a"}},
            "global_learned_hints": "g"}
    changed = {**base, "global_learned_hints": "g2"}
    assert jobs.fingerprint(base, "current") != jobs.fingerprint(changed, "current")
    assert jobs.fingerprint(base, "none") == jobs.fingerprint(changed, "none")
    assert jobs.fingerprint(base, "none") != jobs.fingerprint(
        {**base, "settings": {"detection_prompt": "p", "merge_gap_s": 9.0}}, "none"
    )
