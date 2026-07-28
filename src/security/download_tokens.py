"""Short-lived signed attachment download grants."""

import base64
import hashlib
import hmac
import json
import time

from src.config import settings
from src.security.crypto import persistent_secret


def _key() -> bytes:
    return persistent_secret(settings.session_secret, "session.key").encode()


def issue_download_token(user_id: int, attachment_id: int) -> str:
    payload = json.dumps(
        {
            "user_id": user_id,
            "attachment_id": attachment_id,
            "expires_at": int(time.time()) + settings.attachment_token_ttl_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + signature).decode().rstrip("=")


def verify_download_token(token: str, attachment_id: int) -> int:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, signature = raw.rsplit(b".", 1)
        if not hmac.compare_digest(signature, hmac.new(_key(), payload, hashlib.sha256).digest()):
            raise ValueError
        claims = json.loads(payload)
        if claims["attachment_id"] != attachment_id or claims["expires_at"] < int(time.time()):
            raise ValueError
        return int(claims["user_id"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or expired download token") from exc
