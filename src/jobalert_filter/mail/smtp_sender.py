"""Sends the daily digest to yourself over SMTP (STARTTLS)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from jobalert_filter.config import Config
from jobalert_filter.mail.auth import Credentials


def send_email(
    config: Config, credentials: Credentials, to: str, subject: str, text: str, html: str
) -> None:
    mail = config.mail
    assert mail.smtp_host
    msg = EmailMessage()
    msg["From"] = mail.address
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(mail.smtp_host, mail.smtp_port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        if credentials.method == "oauth2_microsoft":
            auth = credentials.oauth_string()
            smtp.auth("XOAUTH2", lambda challenge=None: auth, initial_response_ok=True)
        else:
            smtp.login(mail.address, credentials.password())
        smtp.send_message(msg)
