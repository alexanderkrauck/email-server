"""Short-lived grants for starting provider account connection flows."""

import base64
import binascii
import hashlib
import hmac
import json
import time

from src.config import settings
from src.security.crypto import persistent_secret


def _key() -> bytes:
    return persistent_secret(settings.session_secret, "session.key").encode()


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_segment(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_account_connect_token(user_id: int) -> str:
    payload = json.dumps(
        {
            "purpose": "gmail-account-connect",
            "user_id": user_id,
            "expires_at": int(time.time()) + settings.account_connect_token_ttl_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    return f"{_encode_segment(payload)}.{_encode_segment(signature)}"


def verify_account_connect_token(token: str) -> int:
    try:
        payload_segment, signature_segment = token.split(".", 1)
        payload = _decode_segment(payload_segment)
        signature = _decode_segment(signature_segment)
        expected = hmac.new(_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        claims = json.loads(payload)
        if (
            claims["purpose"] != "gmail-account-connect"
            or claims["expires_at"] < int(time.time())
        ):
            raise ValueError
        return int(claims["user_id"])
    except (
        binascii.Error,
        UnicodeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid or expired account connection token") from exc
