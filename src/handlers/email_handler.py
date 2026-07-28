"""Authenticated, tenant-scoped HTTP API."""

import json
import logging
import secrets
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy.orm import Session

from src.config import settings
from src.database.connection import get_db
from src.email.email_processor import EmailProcessor
from src.email.smtp_client import SMTPClient
from src.email.smtp_sender import EmailSenderManager
from src.handlers.email_handler_types import SendMailInput
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.security.account_connect_tokens import verify_account_connect_token
from src.security.auth import get_current_user, owned_account_query
from src.security.download_tokens import issue_download_token, verify_download_token
from src.security.provider_tokens import GMAIL_MAIL_SCOPE, encode_oauth_credential
from src.services.attachment_service import owned_attachment, refetch_attachment_bytes
from src.services.mail_service import (
    get_thread,
    owned_account,
    owned_email,
    search_mail,
    search_mail_regex,
    send_mail,
    serialize_attachment,
    serialize_email,
)

logger = logging.getLogger(__name__)
router = APIRouter()
email_sender_manager = EmailSenderManager()
email_processor = EmailProcessor()
CurrentUser = Annotated[User, Depends(get_current_user)]


class GoogleLoginInput(BaseModel):
    credential: str


class MailAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    account_name: EmailStr | None = None
    provider: str = Field(default="imap", pattern="^(imap|zoho|gmail)$")
    host: str
    port: int = Field(default=993, ge=1, le=65535)
    smtp_host: str
    smtp_port: int = Field(default=465, ge=1, le=65535)
    username: str
    password: SecretStr = Field(min_length=1)
    imap_use_ssl: bool = True
    imap_use_tls: bool = False
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False
    enabled: bool = True
    verify_connection: bool = True


class MailAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_name: EmailStr | None = None
    provider: str | None = Field(default=None, pattern="^(imap|zoho|gmail)$")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: SecretStr | None = Field(default=None, min_length=1)
    imap_use_ssl: bool | None = None
    imap_use_tls: bool | None = None
    smtp_use_ssl: bool | None = None
    smtp_use_tls: bool | None = None
    enabled: bool | None = None
    verify_connection: bool = True


class ReplyInput(BaseModel):
    account_id: int
    body_text: str | None = None
    body_html: str | None = None
    cc_addresses: list[EmailStr] = Field(default_factory=list)
    include_original: bool = True
    idempotency_key: str | None = None


class ForwardInput(BaseModel):
    account_id: int
    to_addresses: list[EmailStr]
    body_text: str | None = None
    body_html: str | None = None
    cc_addresses: list[EmailStr] = Field(default_factory=list)
    bcc_addresses: list[EmailStr] = Field(default_factory=list)
    include_attachments: bool = True
    idempotency_key: str | None = None


def account_response(account: SMTPConfig) -> dict:
    result = account.dict()
    result["credential_configured"] = bool(account.credential_ciphertext)
    return result


def validate_transport(account: SMTPConfig) -> None:
    if not account.imap_use_ssl and not account.imap_use_tls:
        raise HTTPException(status_code=400, detail="IMAP must use SSL or STARTTLS")
    if not account.smtp_use_ssl and not account.smtp_use_tls:
        raise HTTPException(status_code=400, detail="SMTP must use SSL or STARTTLS")


async def verify_account_connection(account: SMTPConfig) -> dict[str, bool]:
    """Test a detached account without returning provider error details or credentials."""
    detached = SMTPConfig.create_detached(account)
    imap = SMTPClient(detached)
    try:
        imap_ok = await imap.connect()
    finally:
        await imap.disconnect()
    sender = await email_sender_manager.get_sender(detached)
    try:
        smtp_ok = await sender.connect()
    finally:
        sender.disconnect()
        await email_sender_manager.invalidate(account.id)
    return {"imap": imap_ok, "smtp": smtp_ok}


@router.post("/auth/google")
async def google_login(payload: GoogleLoginInput, request: Request, db: Session = Depends(get_db)):
    """Create a browser session from a Google Identity Services credential."""
    if settings.auth_mode != "google":
        raise HTTPException(status_code=404, detail="Google login is not enabled")
    from src.security.auth import resolve_user, verify_google_id_token

    claims = await verify_google_id_token(payload.credential)
    user = resolve_user(db, claims)
    request.session["identity"] = {
        "sub": user.google_sub,
        "email": user.email,
        "name": user.display_name,
    }
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.post("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"success": True}


@router.get("/me")
async def me(user: CurrentUser):
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.get("/accounts")
async def list_accounts(user: CurrentUser, db: Session = Depends(get_db)):
    return [account_response(account) for account in owned_account_query(db, user).order_by(SMTPConfig.id).all()]


@router.post("/accounts", status_code=201)
async def create_account(payload: MailAccountCreate, user: CurrentUser, db: Session = Depends(get_db)):
    if owned_account_query(db, user).count() >= settings.max_accounts_per_user:
        raise HTTPException(status_code=400, detail="Mail account limit reached")
    if owned_account_query(db, user).filter(SMTPConfig.name == payload.name).first():
        raise HTTPException(status_code=409, detail="An account with this name already exists")
    values = payload.model_dump(exclude={"password", "verify_connection"})
    account = SMTPConfig(owner_user_id=user.id, auth_type="password", **values)
    account.sync_state = "pending" if account.enabled else "disabled"
    account.password = payload.password.get_secret_value()
    validate_transport(account)
    db.add(account)
    db.flush()
    if payload.verify_connection:
        connection = await verify_account_connection(account)
        if not all(connection.values()):
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail={"message": "Provider connection failed", **connection},
            )
    db.commit()
    db.refresh(account)
    logger.info("User %s added mail account %s", user.id, account.id)
    return account_response(account)


@router.get("/accounts/{account_id:int}")
async def get_account(account_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return account_response(owned_account(db, user.id, account_id))


@router.patch("/accounts/{account_id:int}")
async def update_account(
    account_id: int, payload: MailAccountUpdate, user: CurrentUser, db: Session = Depends(get_db)
):
    account = owned_account(db, user.id, account_id)
    if (
        payload.name is not None
        and payload.name != account.name
        and owned_account_query(db, user).filter(SMTPConfig.name == payload.name).first()
    ):
        raise HTTPException(status_code=409, detail="An account with this name already exists")
    values = payload.model_dump(
        exclude_unset=True,
        exclude={"password", "verify_connection"},
    )
    identity_fields = {"provider", "host", "username"}
    identity_changed = {
        field
        for field in identity_fields
        if field in values and getattr(account, field) != values[field]
    }
    for field, value in values.items():
        setattr(account, field, value)
    if payload.password is not None:
        account.password = payload.password.get_secret_value()
    validate_transport(account)
    connection_fields = {
        "provider",
        "host",
        "port",
        "smtp_host",
        "smtp_port",
        "username",
        "password",
        "imap_use_ssl",
        "imap_use_tls",
        "smtp_use_ssl",
        "smtp_use_tls",
    }
    should_verify = payload.verify_connection and bool(payload.model_fields_set & connection_fields)
    if should_verify:
        connection = await verify_account_connection(account)
        if not all(connection.values()):
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail={"message": "Provider connection failed", **connection},
            )
    if identity_changed:
        account.sync_cursors.clear()
        account.initial_sync_complete = False
        account.backfill_complete = False
        account.backfill_processed = 0
        account.backfill_total = None
        account.provider_sync_token = None
        account.sync_page_token = None
    account.sync_lock_token = None
    account.sync_locked_at = None
    account.sync_lock_expires_at = None
    account.last_error_code = None
    account.last_error_message = None
    account.consecutive_failures = 0
    account.retry_at = None
    account.sync_state = (
        "disabled"
        if not account.enabled
        else ("healthy" if account.backfill_complete else "pending")
    )
    db.commit()
    db.refresh(account)
    await email_sender_manager.invalidate(account.id)
    return account_response(account)


@router.delete("/accounts/{account_id:int}")
async def delete_account(account_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    account = owned_account(db, user.id, account_id)
    account.enabled = False
    account.sync_state = "disabled"
    account.sync_lock_token = None
    account.sync_locked_at = None
    account.sync_lock_expires_at = None
    account.retry_at = None
    db.commit()
    return {"success": True, "message": "Account disabled; synced data was retained"}


@router.post("/accounts/{account_id:int}/test")
async def test_account(account_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    account = owned_account(db, user.id, account_id)
    connection = await verify_account_connection(account)
    if not all(connection.values()):
        raise HTTPException(status_code=400, detail=connection)
    return {"imap": "connected", "smtp": "connected"}


@router.post("/accounts/{account_id:int}/sync")
async def sync_account(account_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    owned_account(db, user.id, account_id)
    result = await email_processor.process_server_now(account_id, owner_user_id=user.id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


def _gmail_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{settings.public_base_url.rstrip('/')}/api/v1/accounts/gmail/callback"],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=["openid", "email", GMAIL_MAIL_SCOPE],
        state=state,
    )
    flow.redirect_uri = f"{settings.public_base_url.rstrip('/')}/api/v1/accounts/gmail/callback"
    return flow


def _start_gmail_connection(request: Request, user: User) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    request.session["gmail_oauth_state"] = state
    request.session["gmail_connect_user_id"] = user.id
    flow = _gmail_flow(state)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    return RedirectResponse(authorization_url)


@router.get("/accounts/gmail/connect")
async def connect_gmail(request: Request, user: CurrentUser):
    return _start_gmail_connection(request, user)


@router.get("/accounts/gmail/connect/mcp")
async def connect_gmail_from_mcp(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    try:
        user_id = verify_account_connect_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = db.query(User).filter(User.id == user_id, User.status == "active").first()
    if not user:
        raise HTTPException(status_code=401, detail="Account connection user is unavailable")
    return _start_gmail_connection(request, user)


@router.get("/accounts/gmail/callback")
async def gmail_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    expected_state = request.session.pop("gmail_oauth_state", None)
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    user_id = request.session.pop("gmail_connect_user_id", None)
    user = db.query(User).filter(User.id == user_id, User.status == "active").first()
    if not user:
        raise HTTPException(status_code=401, detail="Account connection session expired")
    flow = _gmail_flow(state)
    flow.fetch_token(code=code)
    credentials = flow.credentials
    if not credentials.refresh_token:
        raise HTTPException(status_code=400, detail="Google did not return an offline refresh token")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        response.raise_for_status()
        provider_identity = response.json()
    email = str(provider_identity["email"]).lower()
    account = (
        owned_account_query(db, user)
        .filter(SMTPConfig.provider == "gmail", SMTPConfig.provider_account_id == provider_identity["sub"])
        .first()
    )
    if not account:
        if owned_account_query(db, user).count() >= settings.max_accounts_per_user:
            raise HTTPException(status_code=400, detail="Mail account limit reached")
        account = SMTPConfig(
            owner_user_id=user.id,
            provider="gmail",
            auth_type="oauth2",
            provider_account_id=provider_identity["sub"],
            name=f"Gmail - {email}",
            account_name=email,
            username=email,
            host="imap.gmail.com",
            port=993,
            smtp_host="smtp.gmail.com",
            smtp_port=465,
            imap_use_ssl=True,
            imap_use_tls=False,
            smtp_use_ssl=True,
            smtp_use_tls=False,
            enabled=True,
            credential_ciphertext=encode_oauth_credential(credentials.refresh_token),
        )
        db.add(account)
    else:
        account.credential_ciphertext = encode_oauth_credential(credentials.refresh_token)
        account.enabled = True
    account.sync_state = "healthy" if account.backfill_complete else "pending"
    account.last_error_code = None
    account.last_error_message = None
    account.consecutive_failures = 0
    account.retry_at = None
    db.commit()
    db.refresh(account)
    return RedirectResponse(
        url=f"{settings.public_base_url.rstrip('/')}?gmail_connected={account.id}"
    )


@router.get("/emails")
async def list_emails(
    user: CurrentUser,
    db: Session = Depends(get_db),
    account_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    deduplicate: str = Query(default="exact", pattern="^(none|exact|mirror)$"),
):
    return search_mail(
        db,
        user,
        account_id=account_id,
        limit=limit,
        cursor=cursor,
        deduplicate=deduplicate,
    )


@router.get("/emails/search")
async def search_emails(
    user: CurrentUser,
    db: Session = Depends(get_db),
    query: str = "",
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    participant: str | None = None,
    participants: list[str] | None = Query(default=None),
    has_attachments: bool = False,
    search_attachments: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    deduplicate: str = Query(default="exact", pattern="^(none|exact|mirror)$"),
):
    return search_mail(
        db,
        user,
        query=query,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        participant=participant,
        participants=participants,
        has_attachments=has_attachments,
        search_attachments=search_attachments,
        limit=limit,
        cursor=cursor,
        deduplicate=deduplicate,
    )


@router.get("/emails/search/regex")
async def search_emails_regex(
    user: CurrentUser,
    pattern: str,
    field: str | None = None,
    fields: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    account_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=25, ge=1, le=50),
    cursor: str | None = None,
    deduplicate: str = Query(default="exact", pattern="^(none|exact|mirror)$"),
):
    return search_mail_regex(
        db,
        user,
        pattern=pattern,
        field=field,
        fields=fields,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
        deduplicate=deduplicate,
    )


@router.get("/emails/{email_id:int}")
async def get_email(email_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return serialize_email(owned_email(db, user.id, email_id), include_body=True)


@router.get("/emails/{email_id:int}/thread")
async def email_thread(email_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return get_thread(db, user, email_id)


@router.get("/attachments/{attachment_id:int}")
async def get_attachment(attachment_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    attachment = owned_attachment(db, user.id, attachment_id)
    result = serialize_attachment(attachment, include_text=True)
    token = issue_download_token(user.id, attachment.id)
    result["download_url"] = (
        f"{settings.public_base_url.rstrip('/')}/api/v1/attachments/{attachment.id}/download?token={token}"
    )
    result["download_expires_in"] = settings.attachment_token_ttl_seconds
    return result


@router.get("/attachments/{attachment_id:int}/download")
async def download_attachment(attachment_id: int, token: str, db: Session = Depends(get_db)):
    try:
        user_id = verify_download_token(token, attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    attachment, payload = await refetch_attachment_bytes(db, user_id, attachment_id)
    from src.email import sanitize_filename

    download_name = sanitize_filename(attachment.filename)
    ascii_name = download_name.encode("ascii", errors="ignore").decode() or "attachment"
    return Response(
        content=payload,
        media_type=attachment.detected_content_type or attachment.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(download_name)}"
            )
        },
    )


@router.post("/send")
async def send_email(payload: SendMailInput, user: CurrentUser, db: Session = Depends(get_db)):
    return await send_mail(db, user, payload)


@router.post("/emails/{email_id:int}/reply")
async def reply_to_email(
    email_id: int, payload: ReplyInput, user: CurrentUser, db: Session = Depends(get_db)
):
    original = owned_email(db, user.id, email_id)
    owned_account(db, user.id, payload.account_id)
    subject = original.subject or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    body_text = payload.body_text or ""
    if payload.include_original:
        quoted = "\n".join(f"> {line}" for line in (original.body_plain or "").splitlines())
        body_text += f"\n\nOn {original.email_date or 'unknown'}, {original.sender} wrote:\n{quoted}"
    send_payload = SendMailInput(
        account_id=payload.account_id,
        to_addresses=[original.sender],
        subject=subject,
        body_text=body_text,
        body_html=payload.body_html,
        cc_addresses=payload.cc_addresses,
        idempotency_key=payload.idempotency_key,
    )
    return await send_mail(
        db,
        user,
        send_payload,
        send_options={"in_reply_to": original.message_id, "references": original.references or original.message_id},
    )


@router.post("/emails/{email_id:int}/forward")
async def forward_email(
    email_id: int, payload: ForwardInput, user: CurrentUser, db: Session = Depends(get_db)
):
    original = owned_email(db, user.id, email_id)
    owned_account(db, user.id, payload.account_id)
    subject = original.subject or ""
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"
    header = (
        "\n\n---------- Forwarded message ----------\n"
        f"From: {original.sender}\nDate: {original.email_date or 'unknown'}\n"
        f"Subject: {original.subject or '(no subject)'}\nTo: {original.recipient}\n\n"
    )
    attachments = []
    if payload.include_attachments:
        for attachment in original.attachments:
            _, binary = await refetch_attachment_bytes(db, user.id, attachment.id)
            attachments.append({"data": binary, "filename": attachment.filename})

    send_payload = SendMailInput(
        account_id=payload.account_id,
        to_addresses=payload.to_addresses,
        subject=subject,
        body_text=(payload.body_text or "") + header + (original.body_plain or ""),
        body_html=payload.body_html,
        cc_addresses=payload.cc_addresses,
        bcc_addresses=payload.bcc_addresses,
        idempotency_key=payload.idempotency_key,
    )
    return await send_mail(db, user, send_payload, attachments=attachments or None)


@router.get("/status")
async def status_view(user: CurrentUser, db: Session = Depends(get_db)):
    accounts = owned_account_query(db, user)
    account_ids = [row.id for row in accounts.all()]
    from src.models.email import EmailLog

    return {
        "status": "running",
        "processor_active": email_processor.processing,
        "accounts": len(account_ids),
        "emails": db.query(EmailLog).filter(EmailLog.smtp_config_id.in_(account_ids)).count()
        if account_ids
        else 0,
    }
