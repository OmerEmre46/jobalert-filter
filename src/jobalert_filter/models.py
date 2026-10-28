"""Plain data objects shared between the pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawEmail:
    """One message as fetched from the mailbox, before parsing."""

    message_id: str
    subject: str
    sender: str
    received_at: datetime | None
    raw: bytes


@dataclass
class Job:
    """One job posting extracted from an alert email."""

    job_id: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    workplace: str = ""  # "remote" | "hybrid" | "onsite" | ""
    source: str = ""  # e.g. "linkedin:jobalerts-noreply"
    received_at: datetime | None = None
    description: str = ""
    extra: list[str] = field(default_factory=list)  # leftover lines such as "Easy Apply"


@dataclass
class RuleResult:
    score: int
    rejected: bool
    reasons: list[str]


@dataclass
class LlmResult:
    score: int
    verdict: str  # "match" | "maybe" | "no"
    reasons: list[str]
    missing: list[str]
    model: str
