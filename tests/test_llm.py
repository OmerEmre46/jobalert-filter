import json

import httpx
import pytest

from jobalert_filter.config import LlmConfig
from jobalert_filter.filter.llm import LlmUnavailable, OllamaScorer, score_assessment
from jobalert_filter.models import Job

JOB = Job(job_id="1", title="Backend Developer Intern", url="u", company="Acme")
TARGETS = ["software", "ai_data", "web", "it"]


def answer(category="software", level="internship", experience_required=False,
           dealbreaker=""):
    return {"category": category, "level": level, "experience_required": experience_required,
            "dealbreaker": dealbreaker}


def scorer(profile, handler):
    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaScorer(LlmConfig(model="test-model"), profile, "tr", client=client)


def replying(data):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": json.dumps(data)}})
    return handler


def test_classification_reply_is_scored_in_code(profile):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return replying(answer())(request)

    result = scorer(profile, handler).score(JOB)
    assert (result.score, result.verdict) == (90, "match")
    assert result.reasons == ["LLM: alan yazılım (hedef alanlarından)"]
    assert result.model.startswith("test-model#p")
    body = seen["body"]
    assert "unspecified" in body["format"]["properties"]["category"]["enum"]
    assert "Backend Developer Intern" in body["messages"][1]["content"]
    assert "CANDIDATE" not in body["messages"][1]["content"]  # no description, no profile


def test_profile_is_sent_when_there_is_a_description(profile):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return replying(answer())(request)

    job = Job(job_id="3", title="Backend Intern", url="u", description="Python, 3rd year+")
    scorer(profile, handler).score(job)
    assert profile.summary.strip() in seen["body"]["messages"][1]["content"]


@pytest.mark.parametrize(("category", "score", "verdict"), [
    ("software", 90, "match"), ("ai_data", 90, "match"), ("it", 90, "match"),
    ("unspecified", 50, "maybe"), ("other_technical", 35, "no"), ("non_technical", 10, "no"),
])
def test_category_drives_the_score(category, score, verdict):
    assert score_assessment(answer(category=category), TARGETS, False)[:2] == (score, verdict)


def test_targets_come_from_the_profile():
    assert score_assessment(answer(category="other_technical"),
                            ["software", "other_technical"], False)[0] == 90
    assert score_assessment(answer(category="web"), ["software"], False)[0] == 10


def test_invented_requirements_ignored_without_description():
    # Seen with real alerts: the model claimed "experience required" from a bare title.
    data = answer(level="experienced", experience_required=True, dealbreaker="Requires exp")
    score, verdict, _, concerns = score_assessment(data, TARGETS, has_description=False)
    assert (score, verdict, concerns) == (90, "match", [])


def test_explicit_requirements_count_with_description():
    score, verdict, _, concerns = score_assessment(answer(experience_required=True), TARGETS,
                                                   has_description=True)
    assert (score, verdict) == (15, "no")
    assert concerns == ["İlan profesyonel iş deneyimi istiyor"]


def test_unclear_level_costs_a_little():
    assert score_assessment(answer(level="unclear"), TARGETS, False)[0] == 80


def test_unknown_enum_values_fall_back(profile):
    result = scorer(profile, replying(answer(category="???", level="???"))).score(JOB)
    assert result.score == 40  # unspecified (50) with unclear level (−10)
    assert "başlıktan anlaşılmıyor" in result.reasons[0]


def test_english_reasons():
    reasons = score_assessment(answer(category="non_technical"), TARGETS, False, "en")[2]
    assert reasons == ["LLM: field non-technical (outside your target fields)"]


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
