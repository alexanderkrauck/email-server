"""Small, deliberate MCP tool surface."""

from datetime import datetime
from typing import Literal

from mcp.types import ToolAnnotations

from src.config import settings
from src.database.connection import SessionLocal
from src.handlers.email_handler import (
    MailAccountCreate,
    MailAccountUpdate,
    create_account,
    update_account,
)
from src.handlers.email_handler_types import SendMailInput
from src.security.account_connect_tokens import issue_account_connect_token
from src.security.auth import current_mcp_user
from src.security.download_tokens import issue_download_token
from src.services.attachment_service import owned_attachment
from src.services.mail_service import (
    MailAccountSummary,
    get_thread as load_thread,
    mail_account_summary,
    owned_email,
    search_mail as lexical_search,
    search_mail_regex as regex_search,
    send_mail as send_message,
    serialize_attachment,
    serialize_email,
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
READ_EXTERNAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE_EXTERNAL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Dates must use ISO-8601 format") from exc


def _transport_flags(mode: Literal["ssl", "starttls"]) -> tuple[bool, bool]:
    return mode == "ssl", mode == "starttls"


def register_mcp_tools(mcp) -> None:
    @mcp.tool(
        name="list_mail_accounts",
        description=(
            "Return exact total message and attachment counts plus every mail account "
            "owned by the signed-in user. Credentials are never returned."
        ),
        annotations=READ_ONLY,
    )
    async def list_mail_accounts() -> MailAccountSummary:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return mail_account_summary(db, user)

    @mcp.tool(
        name="add_mail_account",
        description=(
            "Add an IMAP/SMTP mailbox owned by the signed-in user. The password is "
            "write-only, encrypted at rest, and never included in any response. "
            "Use begin_gmail_connection instead for Gmail OAuth."
        ),
        annotations=WRITE_EXTERNAL,
    )
    async def add_mail_account(
        name: str,
        email_address: str,
        username: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        provider: Literal["imap", "zoho", "gmail"] = "imap",
        imap_port: int = 993,
        smtp_port: int = 465,
        imap_security: Literal["ssl", "starttls"] = "ssl",
        smtp_security: Literal["ssl", "starttls"] = "ssl",
        enabled: bool = True,
        verify_connection: bool = True,
    ) -> dict:
        user = await current_mcp_user()
        imap_use_ssl, imap_use_tls = _transport_flags(imap_security)
        smtp_use_ssl, smtp_use_tls = _transport_flags(smtp_security)
        payload = MailAccountCreate(
            name=name,
            account_name=email_address,
            provider=provider,
            host=imap_host,
            port=imap_port,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            username=username,
            password=password,
            imap_use_ssl=imap_use_ssl,
            imap_use_tls=imap_use_tls,
            smtp_use_ssl=smtp_use_ssl,
            smtp_use_tls=smtp_use_tls,
            enabled=enabled,
            verify_connection=verify_connection,
        )
        with SessionLocal() as db:
            return await create_account(payload, user, db)

    @mcp.tool(
        name="update_mail_account",
        description=(
            "Update one owned mailbox configuration. Omitted fields remain unchanged. "
            "A supplied password is write-only, encrypted at rest, and never returned."
        ),
        annotations=WRITE_EXTERNAL,
    )
    async def update_mail_account(
        account_id: int,
        name: str | None = None,
        email_address: str | None = None,
        username: str | None = None,
        password: str | None = None,
        imap_host: str | None = None,
        smtp_host: str | None = None,
        provider: Literal["imap", "zoho", "gmail"] | None = None,
        imap_port: int | None = None,
        smtp_port: int | None = None,
        imap_security: Literal["ssl", "starttls"] | None = None,
        smtp_security: Literal["ssl", "starttls"] | None = None,
        enabled: bool | None = None,
        verify_connection: bool = True,
    ) -> dict:
        user = await current_mcp_user()
        values = {
            "name": name,
            "account_name": email_address,
            "username": username,
            "password": password,
            "host": imap_host,
            "smtp_host": smtp_host,
            "provider": provider,
            "port": imap_port,
            "smtp_port": smtp_port,
            "enabled": enabled,
        }
        updates = {key: value for key, value in values.items() if value is not None}
        if imap_security is not None:
            updates["imap_use_ssl"], updates["imap_use_tls"] = _transport_flags(
                imap_security
            )
        if smtp_security is not None:
            updates["smtp_use_ssl"], updates["smtp_use_tls"] = _transport_flags(
                smtp_security
            )
        if not updates:
            raise ValueError("At least one mailbox setting must be supplied")
        payload = MailAccountUpdate(
            **updates,
            verify_connection=verify_connection,
        )
        with SessionLocal() as db:
            return await update_account(account_id, payload, user, db)

    @mcp.tool(
        name="begin_gmail_connection",
        description=(
            "Create a short-lived signed URL for connecting a Gmail mailbox with "
            "Google OAuth. Open the returned URL and complete Google consent."
        ),
        annotations=WRITE_EXTERNAL,
    )
    async def begin_gmail_connection() -> dict:
        user = await current_mcp_user()
        token = issue_account_connect_token(user.id)
        return {
            "connect_url": (
                f"{settings.public_base_url.rstrip('/')}"
                f"/api/v1/accounts/gmail/connect/mcp?token={token}"
            ),
            "expires_in": settings.account_connect_token_ttl_seconds,
        }

    @mcp.tool(
        name="search_mail",
        description="Search the signed-in user's mail with indexed lexical search and optional filters.",
        annotations=READ_ONLY,
    )
    async def search_mail(
        query: str = "",
        account_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        participant: str | None = None,
        has_attachments: bool = False,
        search_attachments: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return lexical_search(
                db,
                user,
                query=query,
                account_id=account_id,
                date_from=_date(date_from),
                date_to=_date(date_to),
                participant=participant,
                has_attachments=has_attachments,
                search_attachments=search_attachments,
                limit=limit,
            )

    @mcp.tool(
        name="search_mail_regex",
        description="Run a bounded expert regex search within an account or date scope.",
        annotations=READ_ONLY,
    )
    async def search_mail_regex(
        pattern: str,
        field: Literal["sender", "recipient", "subject", "body", "attachment"],
        account_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 25,
    ) -> list[dict]:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return regex_search(
                db,
                user,
                pattern=pattern,
                field=field,
                account_id=account_id,
                date_from=_date(date_from),
                date_to=_date(date_to),
                limit=limit,
            )

    @mcp.tool(
        name="get_mail",
        description="Retrieve one owned message, its body, and bounded attachment metadata.",
        annotations=READ_ONLY,
    )
    async def get_mail(email_id: int) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return serialize_email(owned_email(db, user.id, email_id), include_body=True)

    @mcp.tool(
        name="get_thread",
        description="Retrieve the thread containing one owned message.",
        annotations=READ_ONLY,
    )
    async def get_thread(email_id: int) -> list[dict]:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return load_thread(db, user, email_id)

    @mcp.tool(
        name="get_attachment",
        description="Get attachment metadata, bounded extracted text, and an expiring original-binary URL.",
        annotations=READ_EXTERNAL,
    )
    async def get_attachment(
        attachment_id: int,
        include_extracted_text: bool = True,
        include_download_url: bool = True,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            attachment = owned_attachment(db, user.id, attachment_id)
            result = serialize_attachment(attachment, include_text=include_extracted_text)
            if include_download_url:
                token = issue_download_token(user.id, attachment.id)
                result["download_url"] = (
                    f"{settings.public_base_url.rstrip('/')}/api/v1/attachments/"
                    f"{attachment.id}/download?token={token}"
                )
                result["download_expires_in"] = settings.attachment_token_ttl_seconds
            return result

    @mcp.tool(
        name="send_mail",
        description="Send mail through an owned account. Use a stable idempotency key when retrying.",
        annotations=WRITE_EXTERNAL,
    )
    async def send_mail(
        account_id: int,
        to_addresses: list[str],
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
        cc_addresses: list[str] | None = None,
        bcc_addresses: list[str] | None = None,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        user = await current_mcp_user()
        payload = SendMailInput(
            account_id=account_id,
            to_addresses=to_addresses,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc_addresses=cc_addresses or [],
            bcc_addresses=bcc_addresses or [],
            reply_to=reply_to,
            idempotency_key=idempotency_key,
        )
        with SessionLocal() as db:
            return await send_message(db, user, payload)
