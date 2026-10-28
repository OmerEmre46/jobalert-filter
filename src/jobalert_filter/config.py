"""Configuration models.

Three files feed the tool, all kept out of git:

* ``config.yaml``  – mailbox, LLM, output and schedule settings
* ``profile.yaml`` – who you are and what you are looking for
* ``.env``         – secrets (mail password); never put them in the YAML files

Relative paths in ``config.yaml`` are resolved against the folder that holds it,
so scheduled runs behave the same as runs from a terminal.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

PROVIDER_DEFAULTS: dict[str, dict[str, object]] = {
    "gmail": {
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
        "auth": "password",
    },
    "outlook": {
        "imap_host": "outlook.office365.com",
        "smtp_host": "smtp-mail.outlook.com",
        "auth": "oauth2_microsoft",
    },
}

PASSWORD_ENV = "JOBALERT_MAIL_PASSWORD"


class ConfigError(Exception):
    pass


class MailConfig(BaseModel):
    provider: Literal["gmail", "outlook", "imap"] = "gmail"
    address: str
    folders: list[str] = Field(default_factory=lambda: ["INBOX"])
    # Emails FROM any of these are read. Add your own old address if you forward
    # alerts from it by hand.
    sender_filter: list[str] = Field(default_factory=lambda: ["linkedin.com"])
    imap_host: str | None = None
    imap_port: int = 993
    smtp_host: str | None = None
    smtp_port: int = 587
    auth: Literal["password", "oauth2_microsoft"] | None = None
    ms_client_id: str | None = None

    @field_validator("sender_filter", mode="before")
    @classmethod
    def _one_or_many(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> MailConfig:
        if not self.sender_filter:
            raise ValueError("mail.sender_filter needs at least one sender")
        defaults = PROVIDER_DEFAULTS.get(self.provider, {})
        for key, value in defaults.items():
            if getattr(self, key) is None:
                setattr(self, key, value)
        if self.auth is None:
            self.auth = "password"
        if not self.imap_host:
            raise ValueError("mail.imap_host is required when provider is 'imap'")
        if self.auth == "oauth2_microsoft" and not self.ms_client_id:
            raise ValueError(
                "mail.ms_client_id is required for Outlook/Hotmail (see README: Outlook setup)"
            )
        return self

    def password(self) -> str:
        value = os.environ.get(PASSWORD_ENV)
        if not value:
            raise ConfigError(f"{PASSWORD_ENV} is not set; put it in your .env file")
        return value


class FetchConfig(BaseModel):
    initial_days: int = 14
    max_messages: int = 500


class LlmConfig(BaseModel):
    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    timeout_seconds: float = 120.0
    rule_weight: float = Field(0.3, ge=0, le=1)


class EnrichConfig(BaseModel):
    """Fetching full descriptions from public job pages. Off by default — read the README first."""

    enabled: bool = False
    delay_seconds: float = Field(5.0, ge=2.0)
    max_per_run: int = Field(20, ge=0, le=100)


class OutputConfig(BaseModel):
    directory: Path = Path("output")
    report_file: str = "report.html"
    tracker_file: str = "tracker.csv"
    show_rejected: bool = True


class DigestConfig(BaseModel):
    enabled: bool = False
    to: str | None = None  # defaults to mail.address
    top_n: int = 15


class Config(BaseModel):
    language: Literal["tr", "en"] = "tr"
    mail: MailConfig
    fetch: FetchConfig = FetchConfig()
    llm: LlmConfig = LlmConfig()
    enrich: EnrichConfig = EnrichConfig()
    output: OutputConfig = OutputConfig()
    digest: DigestConfig = DigestConfig()
    database: Path = Path("data/jobalert.db")
    token_cache: Path = Path(".jobalert/msal_token_cache.json")
    log_file: Path = Path("data/jobalert.log")
    base_dir: Path = Path(".")

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.base_dir / path)


class RulesConfig(BaseModel):
    # Regexes matched against the folded (lower-case, no Turkish diacritics) job title.
    reject_title: list[str] = Field(default_factory=list)
    level_title: list[str] = Field(default_factory=list)
    require_level: bool = False
    field_keywords: list[str] = Field(default_factory=list)
    reject_companies: list[str] = Field(default_factory=list)
    # Location substrings that are acceptable; empty list = any location.
    # Elsewhere costs points; strict_location drops the job instead.
    locations: list[str] = Field(default_factory=list)
    strict_location: bool = False
    allow_remote: bool = True
    allow_hybrid: bool = True
    allow_onsite: bool = True


class Profile(BaseModel):
    summary: str = ""
    department: str = ""
    year_of_study: int | None = None
    expected_graduation: str = ""
    city: str = ""
    languages: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)
    # Job categories the LLM may assign that count as your field; see filter/llm.py.
    target_categories: list[
        Literal["software", "ai_data", "web", "it", "other_technical", "non_technical"]
    ] = Field(default_factory=lambda: ["software", "ai_data", "web", "it"])
    min_score: int = Field(60, ge=0, le=100)
    rules: RulesConfig = RulesConfig()

    def fingerprint(self) -> str:
        """Changes whenever the profile changes, so cached LLM scores get refreshed."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"{path} not found — run `jobalert init` first")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return data


def load_config(path: Path) -> Config:
    path = path.resolve()
    load_dotenv(path.parent / ".env")
    data = _read_yaml(path)
    try:
        config = Config.model_validate({**data, "base_dir": path.parent})
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return config


def load_profile(path: Path) -> Profile:
    data = _read_yaml(path)
    try:
        return Profile.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
