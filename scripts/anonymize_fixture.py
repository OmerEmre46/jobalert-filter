"""Make a raw alert email safe to commit as a test fixture.

    python scripts/anonymize_fixture.py data/samples/0001.eml tests/fixtures/alert_tr.eml \
        --name "Your Name" --name "Your Headline"

What it does:
* keeps only From, Subject and Date; To / Message-ID / routing headers are replaced
* job links become plain https://www.linkedin.com/jobs/view/<id>/ (no tracking ids)
* every other link points to https://www.linkedin.com/, images lose their src
* email addresses (except LinkedIn's no-reply senders) become student@example.com
* each --name value and the name after "intended for" is replaced with "Test User"

It also writes <output>.expected.json with what the parser currently extracts, so you
only have to correct the wrong fields. Always read both files before committing.
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import re
from email.message import EmailMessage
from pathlib import Path

from jobalert_filter.mail.imap_source import parse_headers
from jobalert_filter.models import RawEmail
from jobalert_filter.parse import parse_email

JOB_LINK = re.compile(
    r"https?://[^\s\"'<>]*linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"']*?-)?(\d{6,})"
    r"[^\s\"'<>]*",
    re.I,
)
ANY_LINK = re.compile(r"https?://[^\s\"'<>)]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
INTENDED = re.compile(
    r"((?:intended for|için gönderildi|gönderilmiştir)\s*:?\s*)([^(<\n.]{2,60})", re.I
)


def scrub(text: str, names: list[str]) -> str:
    text = JOB_LINK.sub(lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}/", text)
    text = ANY_LINK.sub(
        lambda m: m.group(0) if re.fullmatch(r"https://www\.linkedin\.com/jobs/view/\d+/",
                                             m.group(0)) else "https://www.linkedin.com/",
        text,
    )
    text = EMAIL.sub(
        lambda m: m.group(0) if "noreply@linkedin.com" in m.group(0).lower()
        else "student@example.com",
        text,
    )
    text = INTENDED.sub(lambda m: m.group(1) + "Test User ", text)
    for name in names:
        text = re.sub(re.escape(name), "Test User", text, flags=re.I)
    text = re.sub(r'(<img\b[^>]*?\bsrc=)("[^"]*"|\'[^\']*\')', r'\1""', text, flags=re.I)
    return text


def body(msg: EmailMessage, subtype: str) -> str | None:
    part = msg.get_body(preferencelist=(subtype,))
    return part.get_content() if part is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--name", action="append", default=[],
                        help="A personal string to replace (repeatable)")
    args = parser.parse_args()

    original = email.message_from_bytes(args.source.read_bytes(), policy=email.policy.default)
    assert isinstance(original, EmailMessage)

    out = EmailMessage()
    out["From"] = scrub(str(original.get("From", "")), args.name)
    out["To"] = "Test User <student@example.com>"
    out["Subject"] = scrub(str(original.get("Subject", "")), args.name)
    out["Date"] = str(original.get("Date", "Mon, 26 Oct 2026 09:00:00 +0300"))
    out["Message-ID"] = f"<{args.target.stem}@fixture.example.com>"

    text = body(original, "plain")
    html = body(original, "html")
    if text is not None:
        out.set_content(scrub(text, args.name))
    if html is not None:
        if text is None:
            out.set_content(scrub(html, args.name), subtype="html")
        else:
            out.add_alternative(scrub(html, args.name), subtype="html")

    args.target.parent.mkdir(parents=True, exist_ok=True)
    raw = out.as_bytes()
    args.target.write_bytes(raw)

    message_id, subject, sender, received = parse_headers(raw)
    jobs = parse_email(RawEmail(message_id, subject, sender, received, raw))
    expected = [{k: getattr(j, k) for k in ("job_id", "title", "company", "location", "workplace")}
                for j in jobs]
    expected_path = args.target.with_suffix(".expected.json")
    expected_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"Wrote {args.target} and {expected_path} ({len(jobs)} job(s)). Review both by hand.")


if __name__ == "__main__":
    main()
