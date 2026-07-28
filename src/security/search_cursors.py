"""Signed, query-bound keyset cursors for exhaustive mail retrieval."""

import base64
import binascii
import hashlib
import hmac
import json
import time
from datetime import datetime

from src.config import settings
from src.security.crypto import persistent_secret


def _key() -> bytes:
    return persistent_secret(settings.session_secret, "session.key").encode()


def _segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _segment(decoded) != value:
        raise binascii.Error("Non-canonical base64url segment")
    return decoded


def issue_search_cursor(
    user_id: int,
    query_hash: str,
    email_date: datetime | None,
    email_id: int,
) -> str:
    payload = json.dumps(
        {
            "purpose": "mail-search",
            "user_id": user_id,
            "query_hash": query_hash,
            "email_date": email_date.isoformat() if email_date else None,
            "email_id": email_id,
            "expires_at": int(time.time()) + settings.search_cursor_ttl_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    return f"{_segment(payload)}.{_segment(signature)}"


def verify_search_cursor(
    token: str,
    user_id: int,
    query_hash: str,
) -> tuple[datetime | None, int]:
    try:
        payload_segment, signature_segment = token.split(".", 1)
        payload = _decode(payload_segment)
        signature = _decode(signature_segment)
        expected = hmac.new(_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        claims = json.loads(payload)
        if (
            claims["purpose"] != "mail-search"
            or int(claims["user_id"]) != user_id
            or claims["query_hash"] != query_hash
            or claims["expires_at"] < int(time.time())
        ):
            raise ValueError
        value = claims.get("email_date")
        return (datetime.fromisoformat(value) if value else None, int(claims["email_id"]))
    except (
        binascii.Error,
        UnicodeError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid, expired, or query-mismatched search cursor") from exc
