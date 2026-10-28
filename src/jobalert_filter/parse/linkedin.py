"""Extract job postings from LinkedIn notification emails.

A single alert email lists several jobs. Every job links to
``linkedin.com/(comm/)jobs/view/<id>``, so the numeric id is the anchor: we group
links by id, take the smallest HTML block that belongs to exactly one id, and read
title / company / location from its text. The plain-text part is the fallback.

LinkedIn changes its templates now and then; when a field comes out wrong, add an
anonymised sample to ``tests/fixtures`` (see ``scripts/anonymize_fixture.py``) and
adjust the heuristics here.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import re
from datetime import datetime
from email.message import EmailMessage

from bs4 import BeautifulSoup, Tag

from jobalert_filter.models import Job, RawEmail
from jobalert_filter.text import clean_lines, fold

JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"']*?-)?(\d{6,})", re.I)

_WORKPLACE = {
    "remote": "remote", "uzaktan": "remote",
    "hybrid": "hybrid", "hibrit": "hybrid",
    "on-site": "onsite", "on site": "onsite", "onsite": "onsite",
    "is yerinde": "onsite", "isyerinde": "onsite", "ofiste": "onsite",
}
_WORKPLACE_RE = re.compile(r"\s*\(([^()]{3,20})\)\s*$")

# Lines that decorate a job card but are not title / company / location.
# Matched against folded text. Count-style badges ("12 applicants", "3 okul mezunu")
# need a digit so that titles like "Yeni Mezun Yazılım Geliştirici" survive.
_NOISE = re.compile(
    r"^(?:view job|see job|apply( now)?|easy apply|kolay basvuru|basvur|hemen basvur"
    r"|ilani goruntule|is ilanini (gor|goruntule)|promoted|tanitilan|one cikan"
    r"|actively (recruiting|hiring)|aktif olarak .*|new|yeni"
    r"|be an early applicant|ilk basvuranlardan biri olun|top applicant"
    r"|.*\d.*\b(applicants?|basvuru\w*|alumni?|alum|mezun\w*|connections?|baglanti\w*"
    r"|employees?|calisan\w*)\b.*"
    r"|.*\b(ago|once)"
    r"|.*\b(posted|yayinlandi)\b.*"
    r"|\d+ (days?|hours?|weeks?|months?|gun|saat|hafta|ay)\b.*"
    r"|[\W\d_]+)$",
    re.I,
)
# Button labels: noise that is not even worth keeping as a badge.
_ACTION = re.compile(
    r"^(?:view job|see job|apply( now)?|basvur|hemen basvur|ilani goruntule"
    r"|is ilanini (gor|goruntule))$",
    re.I,
)
_BULLET_SPLIT =re.compile(r"\s+[·•|]\s+")
_FOOTER_MARKERS = (
    "unsubscribe", "abonelikten", "this email was intended", "bu e-posta",
    "© 20", "linkedin corporation", "help center", "yardim merkezi",
)


def _canonical_url(job_id: str) -> str:
    # Drop tracking parameters: they identify the recipient and must not leak into reports.
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _split_workplace(location: str) -> tuple[str, str]:
    match = _WORKPLACE_RE.search(location)
    if match:
        kind = _WORKPLACE.get(fold(match.group(1)).strip())
        if kind:
            return location[: match.start()].strip(), kind
    kind = _WORKPLACE.get(fold(location).strip())
    if kind:
        return "", kind
    return location, ""


def _is_noise(line: str) -> bool:
    return bool(_NOISE.match(fold(line)))


def _fields_from_lines(
    lines: list[str], title_hint: str = "", tail: bool = False
) -> tuple[str, str, str, list[str]]:
    """Turn a job card's text lines into (title, company, location, leftovers)."""
    expanded: list[str] = []
    for line in lines:
        if any(marker in fold(line) for marker in _FOOTER_MARKERS):
            break
        if JOB_LINK.search(line) or line.lower().startswith(("http://", "https://")):
            continue
        expanded.extend(part.strip() for part in _BULLET_SPLIT.split(line) if part.strip())
    if title_hint in expanded:
        # Anything above the title (e.g. "Your job alert for ...") is not part of the card.
        expanded = expanded[expanded.index(title_hint) :]

    useful = [line for line in expanded if not _is_noise(line)]
    leftovers = [line for line in expanded
                 if _is_noise(line) and not _ACTION.match(fold(line))]
    if tail and len(useful) > 3:
        # Plain text: a header like "Your job alert for ..." can sit above the first card.
        leftovers.extend(useful[:-3])
        useful = useful[-3:]
    title =title_hint or (useful[0] if useful else "")
    rest = [line for line in useful if line != title]
    company = rest[0] if rest else ""
    location = rest[1] if len(rest) > 1 else ""
    # Lines after the location are email chrome ("Premium'u yeniden etkinleştirin",
    # "Tüm iş ilanlarını gör"); only recognised badges are kept as extras.
    return title, company, location, leftovers


def _owner_block(anchor: Tag, job_id: str) -> Tag:
    """Climb from the anchor to the largest ancestor that mentions only this job id."""
    block = anchor
    for parent in anchor.parents:
        if not isinstance(parent, Tag) or parent.name in ("body", "html", "[document]"):
            break
        ids = {m.group(1) for a in parent.find_all("a", href=True)
               if (m := JOB_LINK.search(a["href"]))}
        if ids - {job_id}:
            break
        block = parent
    return block


def _jobs_from_html(html: str) -> list[tuple[str, str, str, str, list[str]]]:
    soup = BeautifulSoup(html, "lxml")
    for hidden in soup.select("style, script, head"):
        hidden.decompose()
    anchors: dict[str, list[Tag]] = {}
    for a in soup.find_all("a", href=True):
        match = JOB_LINK.search(a["href"])
        if match:
            anchors.setdefault(match.group(1), []).append(a)

    results = []
    for job_id, links in anchors.items():
        texts = [t for a in links if (t := a.get_text(" ", strip=True)) and not _is_noise(t)]
        # The longest link text is the title; the logo link is empty and "View job" is noise.
        title_hint = max(texts, key=len) if texts else ""
        block = _owner_block(links[0], job_id)
        for img in block.find_all("img"):
            img.decompose()
        lines = clean_lines(block.get_text("\n"))
        if title_hint:
            # The title may be broken across several text nodes inside the link.
            lines = [line for line in lines if line not in title_hint or line == title_hint]
        title, company, location, extra = _fields_from_lines(lines, title_hint)
        if title:
            results.append((job_id, title, company, location, extra))
    return results


def _jobs_from_text(text: str) -> list[tuple[str, str, str, str, list[str]]]:
    lines = clean_lines(text)
    results = []
    seen: set[str] = set()
    card: list[str] = []
    for line in lines:
        match = JOB_LINK.search(line)
        if not match:
            if re.fullmatch(r"[-=_]{5,}", line):
                card = []
            else:
                card.append(line)
            continue
        job_id = match.group(1)
        if job_id not in seen:
            seen.add(job_id)
            # A card is the few lines just above its link.
            title, company, location, extra = _fields_from_lines(card[-6:], tail=True)
            if title:
                results.append((job_id, title, company, location, extra))
        card = []
    return results


def _body(msg: EmailMessage, subtype: str) -> str:
    part = msg.get_body(preferencelist=(subtype,))
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def source_label(sender: str) -> str:
    match = re.search(r"([\w.+-]+)@([\w.-]+)", sender)
    if not match:
        return "linkedin"
    return f"linkedin:{match.group(1).lower()}"


def _attached_messages(msg: EmailMessage) -> list[EmailMessage]:
    """Emails forwarded *as attachments* (Outlook does this when several are forwarded)."""
    found: list[EmailMessage] = []
    for part in msg.iter_attachments():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            found.extend(m for m in (payload if isinstance(payload, list) else [payload])
                         if isinstance(m, EmailMessage))
        elif (part.get_filename() or "").lower().endswith(".eml"):
            data = part.get_payload(decode=True)
            if data:
                inner = email.message_from_bytes(data, policy=email.policy.default)
                if isinstance(inner, EmailMessage):
                    found.append(inner)
    return found


def _date(msg: EmailMessage, fallback: datetime | None) -> datetime | None:
    try:
        return email.utils.parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else fallback
    except (TypeError, ValueError):
        return fallback


def _jobs_from_message(msg: EmailMessage, sender: str, received: datetime | None) -> list[Job]:
    html = _body(msg, "html")
    found = _jobs_from_html(html) if html else []
    if not found:
        found = _jobs_from_text(_body(msg, "plain"))

    source = source_label(sender)
    jobs: list[Job] = []
    for job_id, title, company, location, extra in found:
        location, workplace = _split_workplace(location)
        jobs.append(
            Job(
                job_id=job_id,
                title=title,
                url=_canonical_url(job_id),
                company=company,
                location=location,
                workplace=workplace,
                source=source,
                received_at=received,
                extra=extra,
            )
        )
    return jobs


def parse_email(raw_email: RawEmail) -> list[Job]:
    msg = email.message_from_bytes(raw_email.raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    jobs = _jobs_from_message(msg, raw_email.sender, raw_email.received_at)
    for inner in _attached_messages(msg):
        jobs += _jobs_from_message(inner, str(inner.get("From", "")),
                                   _date(inner, raw_email.received_at))

    unique: dict[str, Job] = {}
    for job in jobs:
        unique.setdefault(job.job_id, job)
    return list(unique.values())
