"""Small, deliberate MCP tool surface."""

from datetime import datetime
from typing import Literal

from mcp.types import ToolAnnotations

from src.config import settings
from src.database.connection import SessionLocal
from src.handlers.email_handler_types import SendMailInput
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
