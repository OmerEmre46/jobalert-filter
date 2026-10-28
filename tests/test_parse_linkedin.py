import json

import pytest
from conftest import ALERT_TEXT, FIXTURES, make_raw_email

from jobalert_filter.mail.imap_source import parse_headers
from jobalert_filter.models import RawEmail
from jobalert_filter.parse import parse_email


def by_id(jobs):
    return {j.job_id: j for j in jobs}


def test_html_alert_yields_every_job_once():
    jobs = by_id(parse_email(make_raw_email()))
    assert set(jobs) == {"4012345678", "4087654321", "4011112222"}


def test_html_fields():
    jobs = by_id(parse_email(make_raw_email()))
    acme = jobs["4012345678"]
    assert acme.title == "Backend Developer Intern"
    assert acme.company == "Acme Teknoloji"
    assert acme.location == "İstanbul, Türkiye"
    assert acme.workplace == "hybrid"
    assert "12 applicants" in acme.extra
    assert acme.source == "linkedin:jobalerts-noreply"

    gamma = jobs["4011112222"]
    assert gamma.title == "Stajyer – Mobil Uygulama Geliştirici"
    assert gamma.company == "Gamma Yazılım"
    assert gamma.location == "Türkiye"
    assert gamma.workplace == "remote"
    assert gamma.extra == []  # "View job" is a button, not a badge


def test_urls_have_no_tracking_parameters():
    for job in parse_email(make_raw_email()):
        assert job.url == f"https://www.linkedin.com/jobs/view/{job.job_id}/"
        assert "tracking" not in job.url


def test_plain_text_fallback():
    jobs = by_id(parse_email(make_raw_email(html=None, text=ALERT_TEXT)))
    assert set(jobs) == {"4012345678", "4087654321"}
    acme = jobs["4012345678"]
    assert (acme.title, acme.company, acme.location, acme.workplace) == (
        "Backend Developer Intern", "Acme Teknoloji", "İstanbul, Türkiye", "hybrid")
    beta = jobs["4087654321"]
    assert (beta.title, beta.company, beta.workplace) == (
        "Senior Data Engineer", "Beta Bilişim", "onsite")


def test_title_like_yeni_mezun_is_not_treated_as_noise():
    html = ('<table><tr><td><a href="https://www.linkedin.com/jobs/view/4000000001/">'
            "Yeni Mezun Yazılım Geliştirici</a><p>Delta A.Ş. · İzmir, Türkiye</p></td></tr></table>")
    (job,) = parse_email(make_raw_email(html=html, text=None))
    assert job.title == "Yeni Mezun Yazılım Geliştirici"
    assert job.company == "Delta A.Ş."
    assert job.location == "İzmir, Türkiye"


def test_email_without_jobs():
    assert parse_email(make_raw_email(html="<p>Your weekly network update</p>", text="hi")) == []


def test_headers():
    raw = make_raw_email()
    message_id, subject, sender, received = parse_headers(raw.raw)
    assert message_id == "<alert-1@linkedin.com>"
    assert "Backend Developer Intern" in subject
    assert "jobalerts-noreply@linkedin.com" in sender
    assert received is not None and received.year == 2026


# Real (anonymised) samples: tests/fixtures/<name>.eml + <name>.expected.json
REAL_SAMPLES = sorted(FIXTURES.glob("*.eml"))


@pytest.mark.parametrize("eml", REAL_SAMPLES, ids=[p.stem for p in REAL_SAMPLES])
def test_real_samples(eml):
    expected = json.loads(eml.with_suffix(".expected.json").read_text(encoding="utf-8"))
    raw = eml.read_bytes()
    message_id, subject, sender, received = parse_headers(raw)
    jobs = parse_email(RawEmail(message_id, subject, sender, received, raw))
    got = [{k: getattr(j, k) for k in ("job_id", "title", "company", "location", "workplace")}
           for j in jobs]
    assert got == expected
