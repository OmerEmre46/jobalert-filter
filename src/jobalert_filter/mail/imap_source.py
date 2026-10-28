"""Read-only IMAP access.

Folders are opened with EXAMINE (``readonly=True``) and bodies fetched with
``BODY.PEEK[]``, so the mailbox is never modified: nothing is moved, deleted or
marked as read.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import email.utils
import imaplib
import logging
import re
from collections.abc import Iterator
from datetime import date, datetime

from jobalert_filter.config import Config
from jobalert_filter.mail.auth import Credentials
from jobalert_filter.models import RawEmail

log = logging.getLogger(__name__)

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def imap_date(day: date) -> str:
    # IMAP wants English month abbreviations regardless of the system locale.
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_criteria(since: date, senders: list[str]) -> str:
    """IMAP SEARCH for mail since a day from any of the senders (OR is binary in IMAP)."""
    from_terms = [f"FROM {_quote(s)}" for s in senders]
    expr = from_terms[-1]
    for term in reversed(from_terms[:-1]):
        expr = f"OR {term} {expr}"
    return f"(SINCE {imap_date(since)} {expr})"


def parse_headers(raw: bytes) -> tuple[str, str, str, datetime | None]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    message_id = str(msg.get("Message-ID", "")).strip()
    subject = str(msg.get("Subject", "")).strip()
    sender = str(msg.get("From", "")).strip()
    received: datetime | None = None
    if msg.get("Date"):
        try:
            received = email.utils.parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            received = None
    return message_id, subject, sender, received


class ImapSource:
    def __init__(self, config: Config, credentials: Credentials):
        self.config = config
        self.credentials = credentials

    def connect(self) -> imaplib.IMAP4_SSL:
        mail = self.config.mail
        assert mail.imap_host
        conn = imaplib.IMAP4_SSL(mail.imap_host, mail.imap_port, timeout=60)
        if self.credentials.method == "oauth2_microsoft":
            auth = self.credentials.oauth_string().encode()
            conn.authenticate("XOAUTH2", lambda _challenge: auth)
        else:
            conn.login(mail.address, self.credentials.password())
        return conn

    def check(self) -> list[str]:
        """Log in and return the folder names that exist — used by `jobalert check`."""
        conn = self.connect()
        try:
            status, data = conn.list()
            names = []
            if status == "OK":
                for line in data:
                    if isinstance(line, bytes):
                        match = re.search(rb'"([^"]*)"\s*$|(\S+)\s*$', line)
                        if match:
                            # Shown as IMAP sends them (modified UTF-7), which is also
                            # the form `mail.folders` expects.
                            name = match.group(1) or match.group(2)
                            names.append(name.decode("ascii", "replace"))
            return names
        finally:
            conn.logout()

    def fetch(self, since: date, skip_ids: set[str] | None = None) -> Iterator[RawEmail]:
        skip_ids = skip_ids or set()
        mail = self.config.mail
        conn = self.connect()
        try:
            yielded = 0
            for folder in mail.folders:
                status, _ = conn.select(_quote(folder), readonly=True)
                if status != "OK":
                    log.warning("Cannot open folder %r, skipping", folder)
                    continue
                status, data = conn.uid("SEARCH", None,
                                        search_criteria(since, mail.sender_filter))
                if status != "OK" or not data or not data[0]:
                    continue
                uids = data[0].split()
                log.info("%s: %d message(s) from %s since %s", folder, len(uids),
                         ", ".join(mail.sender_filter), since)
                # Newest first, so a max_messages cap keeps the most relevant ones.
                for uid in reversed(uids):
                    if yielded >= self.config.fetch.max_messages:
                        return
                    status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    raw = next(
                        (p[1] for p in parts if isinstance(p, tuple) and len(p) == 2), None
                    )
                    if not raw:
                        continue
                    message_id, subject, sender, received = parse_headers(raw)
                    if not message_id:
                        message_id = f"<{folder}:{uid.decode()}>"
                    if message_id in skip_ids:
                        continue
                    skip_ids.add(message_id)
                    yielded += 1
                    yield RawEmail(message_id, subject, sender, received, raw)
        finally:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                conn.logout()
