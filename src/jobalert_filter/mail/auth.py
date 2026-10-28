"""Mailbox authentication: plain password (Gmail app password, generic IMAP) or
Microsoft OAuth2 for Outlook.com / Hotmail, which no longer accepts passwords over IMAP.

The OAuth flow is the device-code flow: `jobalert auth` prints a code, you sign in
at microsoft.com/devicelogin once, and the refresh token is cached locally so that
scheduled runs can renew access silently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from jobalert_filter.config import Config, ConfigError

log = logging.getLogger(__name__)

MS_AUTHORITY = "https://login.microsoftonline.com/consumers"
# offline_access is added by MSAL itself; listing it explicitly raises an error.
MS_SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "https://outlook.office.com/SMTP.Send",
]


class AuthError(Exception):
    pass


def xoauth2_string(user: str, token: str) -> str:
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


class MicrosoftTokenProvider:
    def __init__(self, client_id: str, cache_path: Path):
        import msal

        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self._cache.deserialize(cache_path.read_text(encoding="utf-8"))
        self._app = msal.PublicClientApplication(
            client_id, authority=MS_AUTHORITY, token_cache=self._cache
        )

    def _save(self) -> None:
        if self._cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(self._cache.serialize(), encoding="utf-8")

    def token(self, address: str) -> str:
        """Return an access token without user interaction, or raise AuthError."""
        accounts = self._app.get_accounts(username=address) or self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(MS_SCOPES, account=accounts[0])
        self._save()
        if not result or "access_token" not in result:
            raise AuthError("No valid Microsoft sign-in cached — run `jobalert auth` once.")
        return result["access_token"]

    def interactive_login(self, echo: Callable[[str], None]) -> str:
        flow = self._app.initiate_device_flow(scopes=MS_SCOPES)
        if "user_code" not in flow:
            raise AuthError(f"Could not start device login: {flow.get('error_description', flow)}")
        echo(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow)
        self._save()
        if "access_token" not in result:
            raise AuthError(f"Sign-in failed: {result.get('error_description', result)}")
        return result["access_token"]


class Credentials:
    """Produces what imaplib/smtplib need, whatever the auth method."""

    def __init__(self, config: Config):
        self.address = config.mail.address
        self.method = config.mail.auth
        self._config = config
        self._ms: MicrosoftTokenProvider | None = None
        if self.method == "oauth2_microsoft":
            if not config.mail.ms_client_id:
                raise ConfigError("mail.ms_client_id is missing")
            self._ms = MicrosoftTokenProvider(
                config.mail.ms_client_id, config.resolve(config.token_cache)
            )

    def password(self) -> str:
        return self._config.mail.password()

    def oauth_string(self) -> str:
        assert self._ms is not None
        return xoauth2_string(self.address, self._ms.token(self.address))

    def interactive_login(self, echo: Callable[[str], None]) -> None:
        if self._ms is None:
            echo("Password authentication is used; nothing to sign in to. Testing the login...")
            return
        self._ms.interactive_login(echo)
