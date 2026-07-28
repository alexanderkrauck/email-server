"""Tenant-scoped search, retrieval, and sending."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.config import settings
from src.handlers.email_handler_types import SendMailInput
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.send_audit import SendAudit
from src.models.smtp_config import SMTPConfig
from src.models.user import User


def owned_account(db: Session, user_id: int, account_id: int) -> SMTPConfig:
    account = (
        db.query(SMTPConfig)
        .filter(SMTPConfig.id == account_id, SMTPConfig.owner_user_id == user_id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Mail account not found")
    return account


def owned_email_query(db: Session, user_id: int):
    return db.query(EmailLog).join(SMTPConfig).filter(SMTPConfig.owner_user_id == user_id)


def owned_email(db: Session, user_id: int, email_id: int) -> EmailLog:
    message = owned_email_query(db, user_id).filter(EmailLog.id == email_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Email not found")
    return message


def serialize_attachment(attachment: EmailAttachment, include_text: bool = False) -> dict[str, Any]:
    result = {
        "id": attachment.id,
        "filename": attachment.filename,
        "content_type": attachment.detected_content_type or attachment.content_type,
        "claimed_content_type": attachment.claimed_content_type,
        "size": attachment.size,
        "sha256": attachment.sha256,
        "extraction_state": attachment.extraction_state,
    }
    if include_text:
        result["text"] = (attachment.text_content or "")[: settings.max_extracted_text_chars]
        result["text_truncated"] = bool(
            attachment.text_content and len(attachment.text_content) > settings.max_extracted_text_chars
        )
    return result


def serialize_email(message: EmailLog, include_body: bool = False, include_attachment_text: bool = False):
    result = {
        "id": message.id,
        "account_id": message.smtp_config_id,
        "sender": message.sender,
        "recipient": message.recipient,
        "subject": message.subject or "",
        "message_id": message.message_id,
        "thread_id": message.provider_thread_id,
        "email_date": message.email_date.isoformat() if message.email_date else None,
        "attachment_count": message.attachment_count,
        "attachments": [
            serialize_attachment(item, include_text=include_attachment_text) for item in message.attachments
        ],
    }
    if include_body:
        plain = message.body_plain or ""
        html = message.body_html or ""
        result["body_plain"] = plain[: settings.max_message_body_chars]
        result["body_html"] = html[: settings.max_message_body_chars]
        result["body_truncated"] = (
            len(plain) > settings.max_message_body_chars
            or len(html) > settings.max_message_body_chars
        )
    return result


def _apply_common_filters(
    query,
    *,
    account_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    participant: str | None,
    has_attachments: bool,
):
    if account_id is not None:
        query = query.filter(EmailLog.smtp_config_id == account_id)
    if date_from:
        query = query.filter(EmailLog.email_date >= date_from)
    if date_to:
        query = query.filter(EmailLog.email_date <= date_to)
    if participant:
        escaped = participant.replace("%", r"\%").replace("_", r"\_")
        query = query.filter(
            or_(
                EmailLog.sender.ilike(f"%{escaped}%", escape="\\"),
                EmailLog.recipient.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    if has_attachments:
        query = query.filter(EmailLog.attachment_count > 0)
    return query


def search_mail(
    db: Session,
    user: User,
    *,
    query: str = "",
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    participant: str | None = None,
    has_attachments: bool = False,
    search_attachments: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if account_id is not None:
        owned_account(db, user.id, account_id)
    q = owned_email_query(db, user.id).filter(EmailLog.deleted_at.is_(None))
    q = _apply_common_filters(
        q,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        participant=participant,
        has_attachments=has_attachments,
    )
    if query.strip():
        parsed_query = func.websearch_to_tsquery("simple", query)
        document = func.to_tsvector(
            "simple",
            func.coalesce(EmailLog.sender, "")
            + " "
            + func.coalesce(EmailLog.recipient, "")
            + " "
            + func.coalesce(EmailLog.subject, "")
            + " "
            + func.coalesce(EmailLog.body_plain, ""),
        )
        condition = document.op("@@")(parsed_query)
        if search_attachments:
            attachment_document = func.to_tsvector(
                "simple", func.coalesce(EmailAttachment.text_content, "")
            )
            attachment_ids = (
                db.query(EmailAttachment.email_log_id)
                .filter(attachment_document.op("@@")(parsed_query))
                .subquery()
            )
            condition = or_(condition, EmailLog.id.in_(select(attachment_ids)))
        q = q.filter(condition)
    messages = q.order_by(EmailLog.email_date.desc().nullslast()).offset(max(0, offset)).limit(min(limit, 100)).all()
    return [serialize_email(message) for message in messages]


def search_mail_regex(
    db: Session,
    user: User,
    *,
    pattern: str,
    field: str,
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    if not pattern or len(pattern) > settings.max_regex_pattern_length:
        raise HTTPException(status_code=400, detail="Regex pattern is empty or too long")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc.msg}") from None
    if field not in {"sender", "recipient", "subject", "body", "attachment"}:
        raise HTTPException(status_code=400, detail="Invalid regex field")
    if account_id is None and date_from is None and date_to is None:
        raise HTTPException(status_code=400, detail="Regex requires an account or date scope")
    if account_id is not None:
        owned_account(db, user.id, account_id)

    db.execute(text(f"SET LOCAL statement_timeout = '{int(settings.regex_statement_timeout_ms)}ms'"))
    q = owned_email_query(db, user.id).filter(EmailLog.deleted_at.is_(None))
    q = _apply_common_filters(
        q,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        participant=None,
        has_attachments=False,
    )
    columns = {
        "sender": EmailLog.sender,
        "recipient": EmailLog.recipient,
        "subject": EmailLog.subject,
        "body": EmailLog.body_plain,
    }
    if field == "attachment":
        matching = (
            q.join(EmailAttachment)
            .filter(EmailAttachment.text_content.op("~*")(pattern))
            .with_entities(EmailLog.id)
            .distinct()
            .subquery()
        )
        q = owned_email_query(db, user.id).filter(EmailLog.id.in_(select(matching)))
    else:
        q = q.filter(columns[field].op("~*")(pattern))
    try:
        messages = q.order_by(EmailLog.email_date.desc().nullslast()).limit(min(limit, 50)).all()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Regex failed or exceeded its execution limit") from exc
    return [serialize_email(message) for message in messages]


def get_thread(db: Session, user: User, email_id: int) -> list[dict[str, Any]]:
    anchor = owned_email(db, user.id, email_id)
    q = owned_email_query(db, user.id).filter(EmailLog.smtp_config_id == anchor.smtp_config_id)
    if anchor.provider_thread_id:
        q = q.filter(EmailLog.provider_thread_id == anchor.provider_thread_id)
    else:
        normalized = re.sub(r"^(re|fwd):\s*", "", anchor.subject or "", flags=re.IGNORECASE)
        if not normalized.strip():
            return [serialize_email(anchor, include_body=True)]
        q = q.filter(func.lower(EmailLog.subject).contains(normalized.lower()))
    return [serialize_email(message, include_body=True) for message in q.order_by(EmailLog.email_date.asc()).limit(100)]


async def send_mail(
    db: Session,
    user: User,
    payload: SendMailInput,
    *,
    attachments: list[dict] | None = None,
    send_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.handlers.email_handler import email_sender_manager

    account = owned_account(db, user.id, payload.account_id)
    recipients = payload.to_addresses + payload.cc_addresses + payload.bcc_addresses
    if not recipients or len(recipients) > settings.max_send_recipients:
        raise HTTPException(status_code=400, detail="Recipient count is outside the configured limit")
    attachment_bytes = sum(len(item.get("data", b"")) for item in attachments or [])
    if attachment_bytes > settings.max_outbound_attachment_bytes:
        raise HTTPException(status_code=400, detail="Outbound attachments exceed the configured limit")
    recent_sends = (
        db.query(SendAudit)
        .filter(
            SendAudit.owner_user_id == user.id,
            SendAudit.created_at >= datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        )
        .count()
    )
    if recent_sends >= settings.max_sends_per_minute:
        raise HTTPException(status_code=429, detail="Outbound send rate limit exceeded")

    canonical_payload = payload.model_dump(mode="json")
    canonical_payload["attachments"] = [
        {
            "filename": item.get("filename"),
            "sha256": hashlib.sha256(item["data"]).hexdigest(),
        }
        for item in attachments or []
    ]
    canonical_payload["send_options"] = send_options or {}
    canonical = json.dumps(canonical_payload, separators=(",", ":"), sort_keys=True)
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    key = payload.idempotency_key or str(uuid.uuid4())
    existing = (
        db.query(SendAudit)
        .filter(SendAudit.owner_user_id == user.id, SendAudit.idempotency_key == key)
        .first()
    )
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="Idempotency key was used for a different request")
        if existing.status == "sent":
            return {"success": True, "idempotent_replay": True, "audit_id": existing.id}
        raise HTTPException(status_code=409, detail=f"Prior send is {existing.status}; it will not be retried")

    audit = SendAudit(
        owner_user_id=user.id,
        smtp_config_id=account.id,
        idempotency_key=key,
        recipients_json=json.dumps(recipients),
        subject=payload.subject,
        request_hash=request_hash,
        status="pending",
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)

    result = await email_sender_manager.send_email_via_config(
        smtp_config_id=account.id,
        owner_user_id=user.id,
        to_addresses=payload.to_addresses,
        subject=payload.subject,
        body_text=payload.body_text,
        body_html=payload.body_html,
        cc_addresses=payload.cc_addresses or None,
        bcc_addresses=payload.bcc_addresses or None,
        reply_to=payload.reply_to,
        attachments=attachments,
        **(send_options or {}),
    )
    audit.status = "sent" if result.get("success") else result.get("delivery_state", "failed")
    audit.provider_message_id = result.get("message_id")
    audit.provider_response = json.dumps(result, default=str)[:10_000]
    audit.completed_at = datetime.now(tz=timezone.utc)
    db.commit()
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "SMTP send failed"))
    return {**result, "audit_id": audit.id, "idempotency_key": key}
