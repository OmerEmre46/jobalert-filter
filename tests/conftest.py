from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

from jobalert_filter.config import Config, Profile, RulesConfig
from jobalert_filter.models import RawEmail
from jobalert_filter.store import Store

FIXTURES = Path(__file__).parent / "fixtures"

ALERT_HTML = """\
<html><head><style>p { margin: 0 }</style></head><body>
<table><tr><td>
  <h2>Your job alert for software intern</h2>
  <table class="job-card"><tr>
    <td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&refId=xyz">
      <img alt="Acme Teknoloji logo" src="https://media.licdn.com/logo.png"></a></td>
    <td>
      <a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Backend Developer Intern</a>
      <p>Acme Teknoloji &middot; &#304;stanbul, T&uuml;rkiye (Hybrid)</p>
      <p>12 applicants</p>
    </td></tr></table>
  <table class="job-card"><tr>
    <td><a href="https://www.linkedin.com/comm/jobs/view/4087654321/?trackingId=def">
      <img alt="Beta logo"></a></td>
    <td>
      <a href="https://www.linkedin.com/comm/jobs/view/4087654321/?trackingId=def">Senior Data Engineer</a>
      <p>Beta Bilişim &middot; Ankara, Türkiye (On-site)</p>
      <p>Actively recruiting</p>
    </td></tr></table>
  <table class="job-card"><tr>
    <td>
      <a href="https://www.linkedin.com/comm/jobs/view/4011112222/?trackingId=ghi">Stajyer – Mobil Uygulama Geliştirici</a>
      <p>Gamma Yazılım</p>
      <p>Türkiye (Remote)</p>
      <a href="https://www.linkedin.com/comm/jobs/view/4011112222/?trackingId=ghi">View job</a>
    </td></tr></table>
  <a href="https://www.linkedin.com/comm/jobs/search?keywords=intern">See all jobs</a>
  <p>This email was intended for Test User (Student). Unsubscribe</p>
</td></tr></table>
</body></html>
"""

ALERT_TEXT = """\
Your job alert for software intern
3 new jobs match your preferences.

Backend Developer Intern
Acme Teknoloji
İstanbul, Türkiye (Hybrid)
View job: https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc

---------------------------------------------------------

Senior Data Engineer
Beta Bilişim
Ankara, Türkiye (On-site)
Actively recruiting
View job: https://www.linkedin.com/comm/jobs/view/4087654321/?trackingId=def

---------------------------------------------------------

This email was intended for Test User (Student).
"""


def make_raw_email(html: str | None = ALERT_HTML, text: str | None = ALERT_TEXT,
                   message_id: str = "<alert-1@linkedin.com>",
                   sender: str = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
                   subject: str = "“software intern”: Acme Teknoloji - Backend Developer Intern",
                   ) -> RawEmail:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "student@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 26 Oct 2026 09:15:00 +0300"
    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    received = datetime(2026, 10, 26, 6, 15, tzinfo=UTC)
    return RawEmail(message_id, subject, sender, received, msg.as_bytes())


@pytest.fixture
def profile() -> Profile:
    return Profile(
        summary="3rd-year Computer Engineering student looking for an internship.",
        department="Computer Engineering",
        skills=["Python", "Kotlin"],
        min_score=60,
        rules=RulesConfig(
            reject_title=[r"\bsenior\b", r"\blead\b", "kıdemli", r"\d+\+? ?(years|yıl)"],
            level_title=[r"\bintern(ship)?\b", r"\bstaj", "stajyer", r"\bjunior\b", "yeni mezun"],
            field_keywords=["backend", "developer", "geliştirici", "mobil", "android", "data"],
            locations=["istanbul", "türkiye"],
        ),
    )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.model_validate({
        "mail": {"provider": "gmail", "address": "student@example.com"},
        "llm": {"enabled": False},
        "base_dir": tmp_path,
    })


@pytest.fixture
def store() -> Store:
    s = Store(Path(":memory:"))
    yield s
    s.close()
