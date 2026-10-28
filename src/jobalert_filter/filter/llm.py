"""Scores a job against your profile with a local LLM served by Ollama.

Small local models (7B) are poor at judging "fit" directly: asked for a 0–100 score
they invent requirements, contradict themselves and drift into other languages. So
the model is only asked to *classify* the posting — its field and level, plus whether
the text explicitly demands experience — through Ollama's structured output. The fit
score and the explanation shown to you are computed here from those answers and the
``target_categories`` in your profile.

If Ollama is not running the pipeline carries on with rule scores only.
"""

from __future__ import annotations

import json
import logging

import httpx

from jobalert_filter.config import LlmConfig, Profile
from jobalert_filter.models import Job, LlmResult

log = logging.getLogger(__name__)

CATEGORIES = ["software", "ai_data", "web", "it", "other_technical", "non_technical",
              "unspecified"]
LEVELS = ["internship", "entry_level", "part_time", "experienced", "unclear"]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "level": {"type": "string", "enum": LEVELS},
        "experience_required": {"type": "boolean"},
        "dealbreaker": {"type": "string"},
    },
    "required": ["category", "level", "experience_required", "dealbreaker"],
}

SYSTEM_PROMPT = """You classify job postings. Titles may be in English or Turkish.

category - what the job's main work is:
  software        writing software: backend, mobile, embedded, game, "coder", "developer",
                  "yazılım", "geliştirici", "programmer"
  ai_data         AI, machine learning, data science, data analysis/engineering, "veri"
  web             web or frontend development (HTML, CSS, JavaScript, React)
  it              information technology, IT support, systems, network, cybersecurity,
                  "bilgi işlem", "bilgi teknolojileri"
  other_technical engineering or technical work outside computing: electrical,
                  mechanical, chemistry or laboratory, manufacturing R&D
  non_technical   HR, sales, marketing, finance, consulting, operations, customer
                  success, administration, programme or policy work
  unspecified     the title does not say what the work is, e.g. "Summer Intern",
                  "Stajyer", "Internship Program"
  A title naming two areas ("IT and Administrative Intern") takes the technical one.

level - "internship", "entry_level", "part_time", "experienced" (asks for professional
  experience or has a senior title) or "unclear".

experience_required - true ONLY if the description explicitly asks for professional
  work experience. With no description, answer false.

dealbreaker - if the description explicitly violates one of the candidate's
  dealbreakers, quote that dealbreaker; otherwise "". With no description, answer "".

Examples (title → category, level):
  "Backend Developer Intern" → software, internship
  "Stajyer – Mobil Uygulama Geliştirici" → software, internship
  "Python Coder (Part-time)" → software, part_time
  "Data Science Intern" → ai_data, internship
  "Computer Vision Research Intern" → ai_data, internship
  "Frontend Developer (React)" → web, unclear
  "Bilgi İşlem Stajyeri" → it, internship
  "IT and Office Support Assistant" → it, unclear
  "Electrical Engineering Intern" → other_technical, internship
  "Chemistry Lab Assistant Intern" → other_technical, internship
  "İnsan Kaynakları Stajyeri" → non_technical, internship
  "Business Consulting Intern" → non_technical, internship
  "Summer Intern 2027" → unspecified, internship
  "Senior Software Engineer" → software, experienced

Judge only from what is written. Do not invent requirements."""

# Points by how the category relates to the profile's target_categories.
TARGET_SCORE = 90
OTHER_TECHNICAL_SCORE = 35  # when other_technical is not itself a target
UNSPECIFIED_SCORE = 50
OFF_TARGET_SCORE = 10
UNCLEAR_LEVEL_PENALTY = 10
BLOCKED_CAP = 15

# Bump when the prompt or the scoring changes, so cached LLM scores are redone.
PROMPT_VERSION = 4

_TEXT = {
    "tr": {
        "category": "LLM: alan {}",
        "names": {"software": "yazılım", "ai_data": "yapay zekâ / veri", "web": "web",
                  "it": "bilgi teknolojileri", "other_technical": "bilişim dışı teknik",
                  "non_technical": "teknik olmayan", "unspecified": "başlıktan anlaşılmıyor"},
        "fits": "hedef alanlarından",
        "off": "hedef alanlarının dışında",
        "experienced": "LLM: ilan deneyimli seviye istiyor",
        "experience": "İlan profesyonel iş deneyimi istiyor",
    },
    "en": {
        "category": "LLM: field {}",
        "names": {"software": "software", "ai_data": "AI / data", "web": "web",
                  "it": "information technology", "other_technical": "non-computing technical",
                  "non_technical": "non-technical", "unspecified": "not clear from the title"},
        "fits": "one of your target fields",
        "off": "outside your target fields",
        "experienced": "LLM: posting asks for an experienced level",
        "experience": "Posting asks for professional work experience",
    },
}

_LANGUAGE_NAMES = {"tr": "Turkish", "en": "English"}


class LlmUnavailable(Exception):
    pass


def score_assessment(data: dict, targets: list[str], has_description: bool,
                     language: str = "tr") -> tuple[int, str, list[str], list[str]]:
    """(score, verdict, reasons, concerns) from the model's answers.

    Level and requirement answers only count when a description was available: from a
    bare title they are guesses, and clearly senior titles are already rejected by the
    keyword rules.
    """
    text = _TEXT.get(language, _TEXT["en"])
    category = data.get("category") if data.get("category") in CATEGORIES else "unspecified"
    level = data.get("level") if data.get("level") in LEVELS else "unclear"

    if category == "unspecified":
        score, relation = UNSPECIFIED_SCORE, ""
    elif category in targets:
        score, relation = TARGET_SCORE, text["fits"]
    elif category == "other_technical":
        score, relation = OTHER_TECHNICAL_SCORE, text["off"]
    else:
        score, relation = OFF_TARGET_SCORE, text["off"]
    name = text["names"][category]
    reasons = [text["category"].format(f"{name} ({relation})" if relation else name)]
    concerns: list[str] = []

    if level == "unclear":
        score -= UNCLEAR_LEVEL_PENALTY
    if has_description:
        if level == "experienced":
            score = min(score, BLOCKED_CAP)
            reasons.append(text["experienced"])
        if data.get("experience_required"):
            score = min(score, BLOCKED_CAP)
            concerns.append(text["experience"])
        dealbreaker = str(data.get("dealbreaker") or "").strip()
        if dealbreaker:
            score = min(score, BLOCKED_CAP)
            concerns.append(dealbreaker)

    score = max(0, min(100, score))
    verdict = "match" if score >= 80 else "maybe" if score >= 50 else "no"
    return score, verdict, reasons, concerns


def profile_text(profile: Profile) -> str:
    parts = [
        f"Summary: {profile.summary.strip()}",
        "Dealbreakers: " + "; ".join(profile.dealbreakers) if profile.dealbreakers else "",
    ]
    return "\n".join(p for p in parts if p)


def job_text(job: Job, max_description: int = 6000) -> str:
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'unknown'}",
    ]
    if job.description:
        parts.append("Description:\n" + job.description[:max_description])
    else:
        parts.append("Description: none (only the title and company are known)")
    return "\n".join(parts)


class OllamaScorer:
    def __init__(self, config: LlmConfig, profile: Profile, language: str = "tr",
                 client: httpx.Client | None = None):
        self.config = config
        self.language = language
        self.targets = list(profile.target_categories)
        self.cache_key = f"{config.model}#p{PROMPT_VERSION}"
        self.profile_block = profile_text(profile)
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
        # The candidate only matters for dealbreakers, which need a description. Without
        # one, showing the profile nudged the model to file vague titles under the
        # candidate's own field ("Summer Intern" → software).
        user = f"JOB POSTING\n{job_text(job)}"
        if job.description:
            user = f"CANDIDATE\n{self.profile_block}\n\n{user}"
        payload = {
            "model": self.config.model,
            "stream": False,
            "format": RESPONSE_SCHEMA,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
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
            if not isinstance(data, dict):
                raise TypeError("reply is not an object")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Unreadable LLM reply for job {job.job_id}: {content[:200]}") from exc
        score, verdict, reasons, concerns = score_assessment(
            data, self.targets, bool(job.description), self.language)
        return LlmResult(score=score, verdict=verdict, reasons=reasons, missing=concerns,
                         model=self.cache_key)
