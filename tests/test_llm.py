import json

import httpx
import pytest

from jobalert_filter.config import LlmConfig
from jobalert_filter.filter.llm import LlmUnavailable, OllamaScorer
from jobalert_filter.models import Job

JOB = Job(job_id="1", title="Backend Developer Intern", url="u", company="Acme")


def scorer(profile, handler):
    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaScorer(LlmConfig(model="test-model"), profile, "tr", client=client)


def test_structured_reply_is_parsed(profile):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        reply = {"score": 87, "verdict": "match", "reasons": ["Python backend stajı"],
                 "missing_requirements": []}
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})

    result = scorer(profile, handler).score(JOB)
    assert (result.score, result.verdict, result.model) == (87, "match", "test-model")
    assert result.reasons == ["Python backend stajı"]
    body = seen["body"]
    assert body["format"]["required"] == ["score", "verdict", "reasons", "missing_requirements"]
    assert "Turkish" in body["messages"][0]["content"]
    assert "Backend Developer Intern" in body["messages"][1]["content"]
    assert "Computer Engineering" in body["messages"][1]["content"]


def test_score_is_clamped_and_verdict_derived(profile):
    def handler(request):
        reply = {"score": 140, "verdict": "???", "reasons": [], "missing_requirements": []}
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})

    result = scorer(profile, handler).score(JOB)
    assert (result.score, result.verdict) == (100, "match")


def test_garbage_reply_raises_value_error(profile):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "not json"}})

    with pytest.raises(ValueError):
        scorer(profile, handler).score(JOB)


def test_connection_error_is_unavailable(profile):
    def handler(request):
        raise httpx.ConnectError("refused")

    s = scorer(profile, handler)
    assert not s.available()
    with pytest.raises(LlmUnavailable):
        s.score(JOB)


def test_available_needs_pulled_model(profile):
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "other:latest"}]})

    assert not scorer(profile, handler).available()

    def handler_ok(request):
        return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})

    assert scorer(profile, handler_ok).available()
