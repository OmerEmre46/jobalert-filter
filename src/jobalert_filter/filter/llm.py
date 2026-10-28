"""Scores a job against your profile with a local LLM served by Ollama.

Ollama's structured output (``format`` = JSON schema) keeps the reply machine-readable.
If Ollama is not running the pipeline carries on with rule scores only.
"""

from __future__ import annotations

import json
import logging

import httpx

from jobalert_filter.config import LlmConfig, Profile
from jobalert_filter.models import Job, LlmResult

log = logging.getLogger(__name__)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["match", "maybe", "no"]},
        "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "missing_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["score", "verdict", "reasons", "missing_requirements"],
}

SYSTEM_PROMPT = """You screen job postings for one candidate.
Judge how well the posting fits the candidate's profile, level and dealbreakers.
Be strict about seniority: postings that need years of professional experience or a
completed degree do not fit a student. When only the title, company and location are
known, judge from those and say so; do not invent requirements.
Score 0-100: 80+ = clearly worth applying, 50-79 = maybe, below 50 = not a fit.
Write reasons and missing requirements in {language}, short (max ~12 words each).
Reply with JSON only."""

_LANGUAGE_NAMES = {"tr": "Turkish", "en": "English"}


class LlmUnavailable(Exception):
    pass


def profile_text(profile: Profile) -> str:
    parts = [
        f"Summary: {profile.summary.strip()}",
        f"Department: {profile.department}",
        f"Year of study: {profile.year_of_study}" if profile.year_of_study else "",
        f"Expected graduation: {profile.expected_graduation}" if profile.expected_graduation
        else "",
        f"City: {profile.city}" if profile.city else "",
        "Languages: " + ", ".join(profile.languages) if profile.languages else "",
        "Skills: " + ", ".join(profile.skills) if profile.skills else "",
        "Target roles: " + "; ".join(profile.target_roles) if profile.target_roles else "",
        "Dealbreakers: " + "; ".join(profile.dealbreakers) if profile.dealbreakers else "",
    ]
    return "\n".join(p for p in parts if p)


def job_text(job: Job, max_description: int = 6000) -> str:
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'unknown'}",
        f"Location: {job.location or 'unknown'}",
        f"Workplace: {job.workplace or 'unknown'}",
    ]
    if job.description:
        parts.append("Description:\n" + job.description[:max_description])
    else:
        parts.append("Description: not available (only the alert email fields are known)")
    return "\n".join(parts)


class OllamaScorer:
    def __init__(self, config: LlmConfig, profile: Profile, language: str = "tr",
                 client: httpx.Client | None = None):
        self.config = config
        self.profile_block = profile_text(profile)
        self.system = SYSTEM_PROMPT.format(language=_LANGUAGE_NAMES.get(language, "English"))
        self.client = client or httpx.Client(base_url=config.base_url,
                                             timeout=config.timeout_seconds)

    def available(self) -> bool:
        try:
            resp = self.client.get("/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Ollama not reachable at %s (%s); using rule scores only",
                        self.config.base_url, exc)
            return False
        models = {m.get("name", "") for m in resp.json().get("models", [])}
        wanted = self.config.model
        if wanted not in models and f"{wanted}:latest" not in models:
            log.warning("Ollama model %r is not pulled; run `ollama pull %s`", wanted, wanted)
            return False
        return True

    def score(self, job: Job) -> LlmResult:
        payload = {
            "model": self.config.model,
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": f"CANDIDATE\n{self.profile_block}\n\n"
                                            f"JOB POSTING\n{job_text(job)}"},
            ],
        }
        try:
            resp = self.client.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmUnavailable(str(exc)) from exc
        content = resp.json().get("message", {}).get("content", "")
        try:
            data = json.loads(content)
            score = max(0, min(100, int(data["score"])))
            verdict = data.get("verdict") if data.get("verdict") in ("match", "maybe", "no") \
                else ("match" if score >= 80 else "maybe" if score >= 50 else "no")
            return LlmResult(
                score=score,
                verdict=verdict,
                reasons=[str(r) for r in data.get("reasons", [])][:4],
                missing=[str(r) for r in data.get("missing_requirements", [])][:4],
                model=self.config.model,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Unreadable LLM reply for job {job.job_id}: {content[:200]}") from exc
