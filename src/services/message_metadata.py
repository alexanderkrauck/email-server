"""Normalized metadata derived from provider messages."""

import hashlib
import json
import re
from email.utils import getaddresses

from src.models.participant import MailParticipant


def content_fingerprint(subject: str | None, body_plain: str | None) -> str | None:
    body = re.sub(r"\s+", " ", (body_plain or "").strip().lower())
    if len(body) < 100:
        return None
    normalized_subject = re.sub(
        r"^\s*((re|fw|fwd):|\[fwd\])\s*",
        "",
        subject or "",
        flags=re.IGNORECASE,
    )
    normalized_subject = re.sub(r"\s+", " ", normalized_subject.strip().lower())
    payload = f"{normalized_subject}\n{body}".encode("utf-8", errors="replace")
    return hashlib.md5(payload, usedforsecurity=False).hexdigest()


def participant_values(email_data: dict) -> list[dict[str, str | None]]:
    sources = [
        ("from", email_data.get("sender")),
        ("to", email_data.get("recipient")),
    ]
    for role, key in (
        ("to", "to_addresses"),
        ("cc", "cc_addresses"),
        ("bcc", "bcc_addresses"),
    ):
        raw = email_data.get(key)
        if not raw:
            continue
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            values = [raw]
        if isinstance(values, str):
            values = [values]
        sources.extend((role, value) for value in values or [])

    result = []
    seen = set()
    for role, value in sources:
        for display_name, address in getaddresses([value or ""]):
            email = address.strip().lower()
            if "@" not in email or (role, email) in seen:
                continue
            seen.add((role, email))
            result.append(
                {
                    "role": role,
                    "email": email[:320],
                    "domain": email.rsplit("@", 1)[1][:255],
                    "display_name": display_name[:500] or None,
                }
            )
    return result


def participant_models(email_log_id: int, email_data: dict) -> list[MailParticipant]:
    return [
        MailParticipant(email_log_id=email_log_id, **values)
        for values in participant_values(email_data)
    ]
