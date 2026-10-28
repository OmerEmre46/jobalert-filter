import email
import email.policy
from datetime import date
from email.message import EmailMessage

from conftest import make_raw_email

from jobalert_filter.config import MailConfig
from jobalert_filter.mail.imap_source import search_criteria
from jobalert_filter.models import RawEmail
from jobalert_filter.parse import parse_email

ME = "Student <student@hotmail.com>"


def forward(attachments: list[RawEmail], inline_html: str = "<p>FYI</p>") -> RawEmail:
    msg = EmailMessage()
    msg["From"] = ME
    msg["To"] = "student@gmail.com"
    msg["Subject"] = "FW: job alerts"
    msg["Message-ID"] = "<fw-1@hotmail.com>"
    msg["Date"] = "Wed, 28 Oct 2026 20:00:00 +0300"
    msg.set_content("FYI")
    msg.add_alternative(inline_html, subtype="html")
    for raw in attachments:
        inner = email.message_from_bytes(raw.raw, policy=email.policy.default)
        msg.add_attachment(inner, filename="alert.eml")
    return RawEmail("<fw-1@hotmail.com>", "FW: job alerts", ME, None, msg.as_bytes())


def test_jobs_inside_forwarded_attachments_are_found():
    first = make_raw_email(message_id="<a>")
    other_html = ('<table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/">'
                  "Data Science Intern</a><p>Epsilon · İstanbul, Türkiye</p></td></tr></table>")
    second = make_raw_email(message_id="<b>", html=other_html, text=None)

    jobs = {j.job_id: j for j in parse_email(forward([first, second]))}

    assert set(jobs) == {"4012345678", "4087654321", "4011112222", "4099999999"}
    acme = jobs["4012345678"]
    assert acme.title == "Backend Developer Intern"
    assert acme.source == "linkedin:jobalerts-noreply"  # the original sender, not me
    assert acme.received_at.year == 2026 and acme.received_at.day == 26  # original date


def test_eml_file_attachment_is_unpacked():
    msg = EmailMessage()
    msg["From"] = ME
    msg["Subject"] = "alerts"
    msg.set_content("see attached")
    msg.add_attachment(make_raw_email().raw, maintype="application", subtype="octet-stream",
                       filename="LinkedIn alert.eml")
    jobs = parse_email(RawEmail("<x>", "alerts", ME, None, msg.as_bytes()))
    assert {j.job_id for j in jobs} == {"4012345678", "4087654321", "4011112222"}


def test_same_job_in_two_attachments_counted_once():
    jobs = parse_email(forward([make_raw_email(message_id="<a>"),
                                make_raw_email(message_id="<b>")]))
    assert len(jobs) == 3


def test_inline_forward_still_finds_job_links():
    html = ("<p>---------- Forwarded message ---------</p>"
            '<table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?x=1">'
            "Backend Developer Intern</a><p>Acme · İstanbul, Türkiye</p></td></tr></table>")
    (job,) = parse_email(forward([], inline_html=html))
    assert (job.job_id, job.title, job.company) == ("4012345678", "Backend Developer Intern",
                                                    "Acme")


def test_search_criteria_with_several_senders():
    since = date(2026, 10, 1)
    assert search_criteria(since, ["linkedin.com"]) == '(SINCE 01-Oct-2026 FROM "linkedin.com")'
    assert search_criteria(since, ["linkedin.com", "me@hotmail.com", "x.com"]) == (
        '(SINCE 01-Oct-2026 OR FROM "linkedin.com" OR FROM "me@hotmail.com" FROM "x.com")'
    )


def test_sender_filter_accepts_a_single_string():
    config = MailConfig(provider="gmail", address="a@gmail.com", sender_filter="linkedin.com")
    assert config.sender_filter == ["linkedin.com"]
