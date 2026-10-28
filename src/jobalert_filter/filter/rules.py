"""Fast keyword rules: hard rejects plus a 0–100 score from the title alone.

Scoring, starting from 40:
  +20 level term (intern, stajyer, junior…)     −10 if there is none
  +10 per field keyword, up to +30              −20 if there is none
  −30 location outside your list (or reject, with strict_location)
So "AI & Machine Learning Intern" in İstanbul scores 80, "Laboratory Intern" 40
and "Backend Developer" (no level term) 50.
"""

from __future__ import annotations

import re

from jobalert_filter.config import Profile
from jobalert_filter.models import Job, RuleResult
from jobalert_filter.text import compile_patterns, fold

BASE_SCORE = 40
LEVEL_BONUS = 20
NO_LEVEL_PENALTY = 10
FIELD_BONUS = 10
FIELD_BONUS_MAX = 30
NO_FIELD_PENALTY = 20
LOCATION_PENALTY = 30

_MESSAGES = {
    "tr": {
        "title": "Başlıkta istenmeyen ifade: “{}”",
        "company": "Hariç tutulan şirket: {}",
        "no_level": "Başlıkta staj/junior ifadesi yok",
        "level": "Seviye uygun: “{}”",
        "fields": "Alan eşleşmesi: {}",
        "no_field": "Başlıkta hedef alanlarından hiçbiri yok",
        "location": "Konum tercihlerin dışında: {}",
        "workplace": "Çalışma şekli istenmiyor: {}",
    },
    "en": {
        "title": "Unwanted term in title: “{}”",
        "company": "Excluded company: {}",
        "no_level": "No intern/junior term in title",
        "level": "Level fits: “{}”",
        "fields": "Field match: {}",
        "no_field": "None of your target fields in title",
        "location": "Outside preferred locations: {}",
        "workplace": "Unwanted workplace type: {}",
    },
}


def _shown(original: str, folded: str, match: re.Match[str]) -> str:
    """The matched words as written in the title ("geliştirici", not "gelistirici")."""
    if len(original) == len(folded):  # true for Turkish letters, so spans line up
        return original[match.start() : match.end()]
    return match.group(0)


def _search(patterns: list[re.Pattern[str]], original: str, folded: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(folded)
        if match:
            return _shown(original, folded, match)
    return None


class RuleEngine:
    def __init__(self, profile: Profile, language: str = "tr"):
        rules = profile.rules
        self.rules = rules
        self.msg = _MESSAGES.get(language, _MESSAGES["en"])
        self.reject_title = compile_patterns(rules.reject_title)
        self.level_title = compile_patterns(rules.level_title)
        self.fields = compile_patterns(rules.field_keywords)
        self.companies = {fold(c).strip() for c in rules.reject_companies if c.strip()}
        self.locations = [fold(loc).strip() for loc in rules.locations if loc.strip()]

    def _outside_locations(self, job: Job) -> bool:
        if job.workplace == "remote" or not self.locations or not job.location:
            return False
        folded = fold(job.location)
        return not any(loc in folded for loc in self.locations)

    def evaluate(self, job: Job) -> RuleResult:
        rules = self.rules
        title = fold(job.title)

        hit = _search(self.reject_title, job.title, title)
        if hit:
            return RuleResult(0, True, [self.msg["title"].format(hit)])
        if job.company and fold(job.company).strip() in self.companies:
            return RuleResult(0, True, [self.msg["company"].format(job.company)])
        allowed = {"remote": rules.allow_remote, "hybrid": rules.allow_hybrid,
                   "onsite": rules.allow_onsite}
        if job.workplace and not allowed.get(job.workplace, True):
            return RuleResult(0, True, [self.msg["workplace"].format(job.workplace)])
        outside = self._outside_locations(job)
        if outside and rules.strict_location:
            return RuleResult(0, True, [self.msg["location"].format(job.location)])

        reasons: list[str] = []
        score = BASE_SCORE
        level = _search(self.level_title, job.title, title)
        if level:
            score += LEVEL_BONUS
            reasons.append(self.msg["level"].format(level))
        elif rules.require_level:
            return RuleResult(0, True, [self.msg["no_level"]])
        else:
            score -= NO_LEVEL_PENALTY
            reasons.append(self.msg["no_level"])

        fields: list[str] = []
        for pattern in self.fields:
            match = pattern.search(title)
            if match:
                word = _shown(job.title, title, match)
                if word.lower() not in {f.lower() for f in fields}:
                    fields.append(word)
        if fields:
            score += min(FIELD_BONUS * len(fields), FIELD_BONUS_MAX)
            reasons.append(self.msg["fields"].format(", ".join(fields)))
        elif self.fields:
            score -= NO_FIELD_PENALTY
            reasons.append(self.msg["no_field"])

        if outside:
            # A district ("Tuzla") or an unlisted city: lower, but leave it visible.
            score -= LOCATION_PENALTY
            reasons.append(self.msg["location"].format(job.location))

        return RuleResult(max(0, min(100, score)), False, reasons)
