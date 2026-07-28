"""Mailbox-provider OAuth token refresh helpers."""

import json

from src.config import settings
from src.security.crypto import decrypt_secret, encrypt_secret

GMAIL_MAIL_SCOPE = "https://mail.google.com/"


def encode_oauth_credential(refresh_token: str, scopes: list[str] | None = None) -> str:
    payload = {
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "scopes": scopes or [GMAIL_MAIL_SCOPE],
    }
    return encrypt_secret(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def refresh_access_token(credential_ciphertext: str) -> str:
    """Refresh a Google mailbox token from an encrypted provider credential."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    payload = json.loads(decrypt_secret(credential_ciphertext))
    credentials = Credentials(
        token=None,
        refresh_token=payload["refresh_token"],
        token_uri=payload["token_uri"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=payload.get("scopes"),
    )
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("Provider did not return an access token")
    return credentials.token
