"""Small, deliberate MCP tool surface."""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
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
from src.security.account_connect_tokens import (
    issue_account_connect_token,
    issue_password_setup_token,
)
from src.security.auth import current_mcp_user
from src.security.download_tokens import issue_download_token
from src.security.mcp_errors import mcp_error_boundary
from src.services.attachment_service import (
    owned_attachment,
    refetch_attachment_bytes,
)
from src.services.mail_service import (
    MailAccountSummary,
    SearchPage,
    mail_account_summary,
    owned_account,
    owned_email,
    serialize_attachment,
    serialize_email,
)
from src.services.mail_service import (
    get_thread as load_thread,
)
from src.services.mail_service import (
    search_mail as lexical_search,
)
from src.services.mail_service import (
    search_mail_regex as regex_search,
)
from src.services.mail_service import (
    send_mail as send_message,
)
from src.services.write_service import create_mail_folder as create_folder_in
from src.services.write_service import delete_mail as delete_message
from src.services.write_service import delete_mail_folder as delete_folder_in
from src.services.write_service import list_mail_folders as load_folders
from src.services.write_service import mark_mail as mark_message
from src.services.write_service import move_mail as move_message_to
from src.services.write_service import rename_mail_folder as rename_folder_in
from src.services.write_service import save_draft as store_draft

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


DESTRUCTIVE_EXTERNAL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
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


def _password_setup_details(user_id: int, account) -> dict:
    token = issue_password_setup_token(
        user_id,
        account.id,
        account.credential_ciphertext,
    )
    return {
        "password_setup_url": (
            f"{settings.public_base_url.rstrip('/')}"
            f"/api/v1/accounts/{account.id}/password/setup?token={token}"
        ),
        "password_setup_expires_in": settings.password_setup_token_ttl_seconds,
    }


SELECTION_DOC = (
    'Selects messages the same way search_mail does: pass email_ids for specific messages, or the same filters (query, participants, folders, date_from/to, is_unread, has_attachments) to act on everything that matches. One call, one connection, one command per folder -- triaging thousands of newsletters does not mean thousands of tool calls. The response reports how many matched versus how many were affected, so a truncated batch is never mistaken for a finished one; repeat the same call to continue. limit defaults to everything that matched. '
)


def register_mcp_tools(mcp) -> None:
    @mcp.tool(
        name="list_mail_accounts",
        description=(
            "Return exact total message and attachment counts plus every mail account "
            "owned by the signed-in user. Credentials are never returned."
        ),
        annotations=READ_ONLY,
    )
    @mcp_error_boundary
    async def list_mail_accounts() -> MailAccountSummary:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return mail_account_summary(db, user)

    @mcp.tool(
        name="add_mail_account",
        description=(
            "Add an IMAP/SMTP mailbox owned by the signed-in user. Password is optional: "
            "supply it directly when supported, or omit it and open the returned "
            "password-only setup URL. Configuration is retained when connection tests "
            "fail. Use begin_gmail_connection instead for Gmail OAuth."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def add_mail_account(
        name: str,
        email_address: str,
        username: str,
        imap_host: str,
        smtp_host: str,
        password: str | None = None,
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
            result = await create_account(payload, user, db)
            if not result["credential_configured"]:
                account = owned_account(db, user.id, result["id"])
                result.update(_password_setup_details(user.id, account))
            return result

    @mcp.tool(
        name="update_mail_account",
        description=(
            "Update one owned mailbox configuration. Omitted fields remain unchanged. "
            "A supplied password is write-only, encrypted at rest, and never returned. "
            "Saved settings and credentials are retained when a connection test fails."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
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
        name="begin_mail_account_password_setup",
        description=(
            "Create a short-lived browser URL that asks only for the password of an "
            "existing IMAP/SMTP account. Use this when the MCP client will not transmit "
            "passwords. The password is stored independently of connection-test results."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def begin_mail_account_password_setup(account_id: int) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            account = owned_account(db, user.id, account_id)
            if account.auth_type != "password":
                raise ValueError("OAuth mailboxes do not accept mailbox passwords")
            return {
                "account_id": account.id,
                **_password_setup_details(user.id, account),
            }

    @mcp.tool(
        name="begin_gmail_connection",
        description=(
            "Create a short-lived signed URL for connecting a Gmail mailbox with "
            "Google OAuth. Open the returned URL and complete Google consent."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
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
        description=(
            "Exhaustively search owned mail with exact counts, stable cursor pagination, "
            "participant/domain facets, match provenance, deduplication, and sync coverage. "
            "Trash, Spam and Junk folders are excluded unless named in folders, or "
            "exclude_folders is set to an empty list. "
            "match='stemmed' finds inflected forms of a word, so 'invoice' also finds "
            "'invoices' and 'Rechnung' also finds 'Rechnungen'; use match='exact' for "
            "identifiers, order numbers and surnames, which a stemmer would widen. "
            "is_unread, is_flagged and is_answered filter on read state; messages whose "
            "provider never reported flags cannot match either value and are counted in a "
            "FLAG_STATE_UNKNOWN warning rather than being silently dropped."
        ),
        annotations=READ_ONLY,
    )
    @mcp_error_boundary
    async def search_mail(
        query: str = "",
        account_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        participant: str | None = None,
        participants: list[str] | None = None,
        has_attachments: bool = False,
        search_attachments: bool = False,
        folders: list[str] | None = None,
        exclude_folders: list[str] | None = None,
        is_unread: bool | None = None,
        is_flagged: bool | None = None,
        is_answered: bool | None = None,
        match: Literal["stemmed", "exact"] = "stemmed",
        limit: int = 50,
        cursor: str | None = None,
        deduplicate: Literal["none", "exact", "mirror"] = "exact",
    ) -> SearchPage:
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
                participants=participants,
                has_attachments=has_attachments,
                search_attachments=search_attachments,
                folders=folders,
                exclude_folders=exclude_folders,
                is_unread=is_unread,
                is_flagged=is_flagged,
                is_answered=is_answered,
                match=match,
                limit=limit,
                cursor=cursor,
                deduplicate=deduplicate,
            )

    @mcp.tool(
        name="mark_mail",
        description=(
            "Set or clear the read or flagged state of mail, in the mailbox itself and "
            "then in the index. " + SELECTION_DOC +
            "Acts on the live copy of each message, not on a copy in Trash, and records "
            "nothing locally that the server did not confirm."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def mark_mail(
        mark: Literal["read", "unread", "flagged", "unflagged"],
        email_ids: list[int] | None = None,
        query: str = "",
        account_id: int | None = None,
        participants: list[str] | None = None,
        folders: list[str] | None = None,
        exclude_folders: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_unread: bool | None = None,
        is_flagged: bool | None = None,
        has_attachments: bool = False,
        search_attachments: bool = False,
        match: Literal["stemmed", "exact"] = "stemmed",
        limit: int | None = None,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await mark_message(
                db,
                user,
                mark=mark,
                email_ids=email_ids,
                query=query,
                account_id=account_id,
                participants=participants,
                folders=folders,
                exclude_folders=exclude_folders,
                date_from=_date(date_from),
                date_to=_date(date_to),
                is_unread=is_unread,
                is_flagged=is_flagged,
                has_attachments=has_attachments,
                search_attachments=search_attachments,
                match=match,
                limit=limit,
            )

    @mcp.tool(
        name="list_mail_folders",
        description=(
            "List the folders of one mailbox, with the role the server declares for "
            "each (inbox, sent, drafts, trash, junk, archive) and how many indexed "
            "messages are filed there. Call this before move_mail: folder names are "
            "server-specific, and INBOX.Trash, [Google Mail]/Trash and Deleted "
            "Messages are all real names for the same idea."
        ),
        annotations=READ_EXTERNAL,
    )
    @mcp_error_boundary
    async def list_mail_folders(account_id: int) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await load_folders(db, user, account_id=account_id)

    @mcp.tool(
        name="move_mail",
        description=(
            "Move mail into another folder of the same mailbox. The folder must exist: "
            "call list_mail_folders for exact names, or create_mail_folder first. " +
            SELECTION_DOC +
            "To delete, use delete_mail rather than moving to Trash by hand."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def move_mail(
        folder: str,
        email_ids: list[int] | None = None,
        query: str = "",
        account_id: int | None = None,
        participants: list[str] | None = None,
        folders: list[str] | None = None,
        exclude_folders: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_unread: bool | None = None,
        is_flagged: bool | None = None,
        has_attachments: bool = False,
        search_attachments: bool = False,
        match: Literal["stemmed", "exact"] = "stemmed",
        limit: int | None = None,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await move_message_to(
                db,
                user,
                folder=folder,
                email_ids=email_ids,
                query=query,
                account_id=account_id,
                participants=participants,
                folders=folders,
                exclude_folders=exclude_folders,
                date_from=_date(date_from),
                date_to=_date(date_to),
                is_unread=is_unread,
                is_flagged=is_flagged,
                has_attachments=has_attachments,
                search_attachments=search_attachments,
                match=match,
                limit=limit,
            )

    @mcp.tool(
        name="delete_mail",
        description=(
            "Move mail to Trash. Reversible: the messages stay in the mailbox and in "
            "the index, filed under Trash, and search excludes Trash by default. " +
            SELECTION_DOC +
            "permanent=true destroys them and is refused unless every selected message "
            "is already in Trash, so removing mail always takes two deliberate steps."
        ),
        annotations=DESTRUCTIVE_EXTERNAL,
    )
    @mcp_error_boundary
    async def delete_mail(
        permanent: bool = False,
        email_ids: list[int] | None = None,
        query: str = "",
        account_id: int | None = None,
        participants: list[str] | None = None,
        folders: list[str] | None = None,
        exclude_folders: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_unread: bool | None = None,
        is_flagged: bool | None = None,
        has_attachments: bool = False,
        search_attachments: bool = False,
        match: Literal["stemmed", "exact"] = "stemmed",
        limit: int | None = None,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await delete_message(
                db,
                user,
                permanent=permanent,
                email_ids=email_ids,
                query=query,
                account_id=account_id,
                participants=participants,
                folders=folders,
                exclude_folders=exclude_folders,
                date_from=_date(date_from),
                date_to=_date(date_to),
                is_unread=is_unread,
                is_flagged=is_flagged,
                has_attachments=has_attachments,
                search_attachments=search_attachments,
                match=match,
                limit=limit,
            )

    @mcp.tool(
        name="create_mail_folder",
        description=(
            "Create a folder in one mailbox, so mail can be filed somewhere that does "
            "not exist yet. Subscribes to it, so other mail clients show it too. "
            "Returns created=false if a folder of that name was already there."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def create_mail_folder(account_id: int, name: str) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await create_folder_in(db, user, account_id=account_id, name=name)

    @mcp.tool(
        name="delete_mail_folder",
        description=(
            "Remove a folder from one mailbox. Any mail still in it is moved to Trash "
            "first, so deleting a folder never destroys messages. Refuses for INBOX and "
            "for any folder the server declares as its Sent, Drafts, Trash, Spam or "
            "Archive, refuses while it still contains nested folders, and refuses if "
            "the server reports messages the index did not know about unless force=true."
        ),
        annotations=DESTRUCTIVE_EXTERNAL,
    )
    @mcp_error_boundary
    async def delete_mail_folder(account_id: int, name: str, force: bool = False) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await delete_folder_in(db, user, account_id=account_id, name=name, force=force)

    @mcp.tool(
        name="rename_mail_folder",
        description=(
            "Rename a folder, keeping its mail and any nested folders with it. Refuses "
            "for INBOX and for folders the server declares a special use for."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def rename_mail_folder(account_id: int, name: str, new_name: str) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await rename_folder_in(
                db, user, account_id=account_id, name=name, new_name=new_name
            )

    @mcp.tool(
        name="save_draft",
        description=(
            "Write a draft into the mailbox's Drafts folder, where any mail client "
            "will find it. Nothing is sent. Pass reply_to_email_id to thread the draft "
            "onto an existing message. Use send_mail to actually send."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def save_draft(
        account_id: int,
        to_addresses: list[str],
        subject: str,
        body_text: str = "",
        body_html: str = "",
        cc_addresses: list[str] | None = None,
        reply_to_email_id: int | None = None,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return await store_draft(
                db,
                user,
                account_id=account_id,
                to_addresses=to_addresses,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                cc_addresses=cc_addresses,
                reply_to_email_id=reply_to_email_id,
            )

    @mcp.tool(
        name="search_mail_regex",
        description=(
            "Run bounded Unicode regex search across one or more fields. Requires an "
            "account or date scope and returns exact counts, matches, and a stable cursor."
        ),
        annotations=READ_ONLY,
    )
    @mcp_error_boundary
    async def search_mail_regex(
        pattern: str,
        field: Literal["sender", "recipient", "subject", "body", "attachment"]
        | None = None,
        fields: list[
            Literal["sender", "recipient", "subject", "body", "attachment"]
        ]
        | None = None,
        account_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        folders: list[str] | None = None,
        exclude_folders: list[str] | None = None,
        is_unread: bool | None = None,
        is_flagged: bool | None = None,
        is_answered: bool | None = None,
        limit: int = 25,
        cursor: str | None = None,
        deduplicate: Literal["none", "exact", "mirror"] = "exact",
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return regex_search(
                db,
                user,
                pattern=pattern,
                field=field,
                fields=fields,
                account_id=account_id,
                date_from=_date(date_from),
                date_to=_date(date_to),
                folders=folders,
                exclude_folders=exclude_folders,
                is_unread=is_unread,
                is_flagged=is_flagged,
                is_answered=is_answered,
                limit=limit,
                cursor=cursor,
                deduplicate=deduplicate,
            )

    @mcp.tool(
        name="get_mail",
        description=(
            "Retrieve one owned message with bounded body output. Plain text is the "
            "default; request HTML explicitly when formatting is required."
        ),
        annotations=READ_ONLY,
    )
    @mcp_error_boundary
    async def get_mail(
        email_id: int,
        body_format: Literal["plain", "html", "both", "metadata"] = "plain",
        max_body_chars: int = 50_000,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return serialize_email(
                owned_email(db, user.id, email_id),
                body_format=body_format,
                max_body_chars=max_body_chars,
            )

    @mcp.tool(
        name="get_thread",
        description=(
            "Reconstruct a thread using provider IDs or RFC reply headers, returning "
            "bounded plain text by default plus reconstruction confidence."
        ),
        annotations=READ_ONLY,
    )
    @mcp_error_boundary
    async def get_thread(
        email_id: int,
        body_format: Literal["plain", "html", "both", "metadata"] = "plain",
        max_body_chars: int = 20_000,
        limit: int = 100,
    ) -> dict:
        user = await current_mcp_user()
        with SessionLocal() as db:
            return load_thread(
                db,
                user,
                email_id,
                body_format=body_format,
                max_body_chars=max_body_chars,
                limit=limit,
            )

    @mcp.tool(
        name="get_attachment",
        description="Get attachment metadata, bounded extracted text, and an expiring original-binary URL.",
        annotations=READ_EXTERNAL,
    )
    @mcp_error_boundary
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
        description=(
            "Send mail through an owned account. Existing owned attachment IDs are "
            "refetched and checksum-verified ephemerally. For a real reply in the "
            "existing email thread, pass reply_to_email_id from search_mail/get_mail; "
            "the same account_id must own that message. Use a stable idempotency key "
            "when retrying."
        ),
        annotations=WRITE_EXTERNAL,
    )
    @mcp_error_boundary
    async def send_mail(
        account_id: int,
        to_addresses: list[str],
        subject: str,
        reply_to_email_id: int | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        cc_addresses: list[str] | None = None,
        bcc_addresses: list[str] | None = None,
        reply_to: str | None = None,
        attachment_ids: list[int] | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        user = await current_mcp_user()
        payload = SendMailInput(
            account_id=account_id,
            to_addresses=to_addresses,
            subject=subject,
            reply_to_email_id=reply_to_email_id,
            body_text=body_text,
            body_html=body_html,
            cc_addresses=cc_addresses or [],
            bcc_addresses=bcc_addresses or [],
            reply_to=reply_to,
            idempotency_key=idempotency_key,
        )
        with SessionLocal() as db:
            selected_ids = list(dict.fromkeys(attachment_ids or []))
            if len(selected_ids) > settings.max_outbound_attachments:
                raise HTTPException(
                    status_code=400,
                    detail="Too many outbound attachments",
                )
            attachments = []
            for attachment_id in selected_ids:
                attachment, binary = await refetch_attachment_bytes(
                    db,
                    user.id,
                    attachment_id,
                )
                attachments.append(
                    {
                        "data": binary,
                        "filename": attachment.filename,
                    }
                )
            return await send_message(
                db,
                user,
                payload,
                attachments=attachments or None,
            )
