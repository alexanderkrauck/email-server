"""Mutating a mailbox on the owner's behalf.

Only flags for now. A flag is the one write whose worst failure is a wrong
boolean that the next reconcile corrects; a move or a delete relocates mail, and
neither belongs here until this path has proven itself.

Three things every write must do, in this order: prove the caller owns the
message, prove the message has an address upstream, and hold the mailbox lease
so that no census is reading sequence numbers while the write lands.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.config import settings
from src.email.draft_builder import build_draft
from src.email.email_processor import (
    acquire_mailbox_lease,
    release_mailbox_lease,
    upsert_placement,
)
from src.email.imap_writer import (
    ImapWriteError,
    append_message,
    list_folders,
    move_message,
    store_flags,
)
from src.email.message_flags import apply_flag_state
from src.email.smtp_client import SMTPClient
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import (
    is_excluded_folder,
    owned_account,
    owned_email,
    serialize_email,
)

logger = logging.getLogger(__name__)

MARKS = {
    "read": ([r"\Seen"], []),
    "unread": ([], [r"\Seen"]),
    "flagged": ([r"\Flagged"], []),
    "unflagged": ([], [r"\Flagged"]),
}

_recent_writes: dict[int, list[datetime]] = {}


def _rate_limit(user_id: int) -> None:
    """Per owner, mirroring send_mail. A global counter lets one tenant starve the rest."""
    now = datetime.now(tz=timezone.utc)
    window = now - timedelta(minutes=1)
    recent = [stamp for stamp in _recent_writes.get(user_id, []) if stamp > window]
    if len(recent) >= settings.max_writes_per_minute:
        raise HTTPException(status_code=429, detail="Mailbox write rate limit exceeded")
    recent.append(now)
    _recent_writes[user_id] = recent


def writable_placement(db: Session, message: EmailLog) -> MessagePlacement:
    """The copy of a message a write should address.

    Prefer one that is addressable -- it has a UID -- and outside Trash, so that
    marking a message read does not act on the deleted copy while the live one
    keeps its old state.
    """
    placements = (
        db.query(MessagePlacement).filter(MessagePlacement.email_log_id == message.id).all()
    )
    if not placements:
        raise HTTPException(
            status_code=409,
            detail=(
                "This message has no known folder, so it cannot be addressed upstream. "
                "Run scripts/repair_placements.py to locate messages stored before "
                "folders were tracked."
            ),
        )
    suffixes = settings.excluded_folder_suffixes
    ranked = sorted(
        placements,
        key=lambda placement: (
            placement.uid is None,
            is_excluded_folder(placement.folder, suffixes),
            placement.folder or "",
        ),
    )
    best = ranked[0]
    if best.uid is None:
        raise HTTPException(status_code=409, detail="This message has no upstream UID to address")
    return best


def _writable_account(db: Session, user: User, message: EmailLog) -> SMTPConfig:
    account = db.query(SMTPConfig).filter(SMTPConfig.id == message.smtp_config_id).one()
    if account.provider == "gmail" and account.auth_type == "oauth2":
        raise HTTPException(
            status_code=501,
            detail="Gmail API accounts do not support flag writes yet",
        )
    if not account.credential_ciphertext:
        raise HTTPException(status_code=400, detail="Mailbox password is not configured")
    return account


async def mark_mail(db: Session, user: User, *, email_id: int, mark: str) -> dict:
    """Set or clear one flag on a message, upstream first, then locally."""
    if mark not in MARKS:
        raise HTTPException(status_code=400, detail=f"Unknown mark: {mark}")
    message = owned_email(db, user.id, email_id)
    account = _writable_account(db, user, message)
    placement = writable_placement(db, message)
    _rate_limit(user.id)

    add, remove = MARKS[mark]
    # A sync pass holds the lease for seconds at a time, so a collision is
    # ordinary rather than exceptional. Wait it out briefly instead of returning a
    # failure the caller would only retry itself.
    token = None
    deadline = datetime.now(tz=timezone.utc) + timedelta(
        seconds=settings.mail_write_lease_wait_seconds
    )
    while True:
        token = acquire_mailbox_lease(db, account, seconds=settings.sync_lease_seconds)
        if token or datetime.now(tz=timezone.utc) >= deadline:
            break
        await asyncio.sleep(1)
    if not token:
        raise HTTPException(
            status_code=409,
            detail=(
                "This mailbox has been synchronizing for longer than this write was "
                "willing to wait; retry shortly"
            ),
        )

    client = SMTPClient(SMTPConfig.create_detached(account))
    try:
        if not await client.connect():
            raise HTTPException(status_code=502, detail="Could not connect to the mailbox")
        try:
            confirmed = await store_flags(
                client, placement.folder, [placement.uid], add=add, remove=remove
            )
        except ImapWriteError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # A cancelled aioimaplib command leaves pending_sync_command set and every
        # later command, including logout, waits on it forever.
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception as exc:
            logger.warning("Discarding wedged IMAP session after write: %s", exc)
        release_mailbox_lease(db, token)

    raw = confirmed.get(placement.uid)
    if raw is None:
        raise HTTPException(
            status_code=502,
            detail="The server did not confirm the new flags; nothing was changed locally",
        )
    apply_flag_state(message, raw)
    db.commit()
    db.refresh(message)
    return {
        "success": True,
        "mark": mark,
        "folder": placement.folder,
        "message": serialize_email(message),
    }


async def _with_mailbox(db: Session, account: SMTPConfig, operation):
    """Run one operation against a mailbox while holding its lease."""
    token = None
    deadline = datetime.now(tz=timezone.utc) + timedelta(
        seconds=settings.mail_write_lease_wait_seconds
    )
    while True:
        token = acquire_mailbox_lease(db, account, seconds=settings.sync_lease_seconds)
        if token or datetime.now(tz=timezone.utc) >= deadline:
            break
        await asyncio.sleep(1)
    if not token:
        raise HTTPException(
            status_code=409,
            detail="This mailbox has been synchronizing longer than this write would wait; retry shortly",
        )
    client = SMTPClient(SMTPConfig.create_detached(account))
    try:
        if not await client.connect():
            raise HTTPException(status_code=502, detail="Could not connect to the mailbox")
        try:
            return await operation(client)
        except ImapWriteError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception as exc:
            logger.warning("Discarding wedged IMAP session after write: %s", exc)
        release_mailbox_lease(db, token)


async def list_mail_folders(db: Session, user: User, *, account_id: int) -> dict:
    """Every folder in one mailbox, so a move has real names to target."""
    account = owned_account(db, user.id, account_id)
    if account.provider == "gmail" and account.auth_type == "oauth2":
        raise HTTPException(status_code=501, detail="Gmail API accounts expose labels, not folders")
    folders = await _with_mailbox(db, account, lambda client: list_folders(client))
    counts = dict(
        db.query(MessagePlacement.folder, func.count())
        .join(EmailLog, EmailLog.id == MessagePlacement.email_log_id)
        .filter(EmailLog.smtp_config_id == account_id, EmailLog.deleted_at.is_(None))
        .group_by(MessagePlacement.folder)
        .all()
    )
    for folder in folders:
        folder["indexed_message_count"] = int(counts.get(folder["name"], 0))
    return {"account_id": account_id, "folders": folders}


def _role_folder(folders: list[dict], role: str, suffixes: list[str]) -> str | None:
    """The folder serving a role, by its own declaration first and its name second."""
    declared = next((f["name"] for f in folders if f["special_use"] == role), None)
    if declared:
        return declared
    return next((f["name"] for f in folders if is_excluded_folder(f["name"], suffixes)), None)


async def move_mail(db: Session, user: User, *, email_id: int, folder: str) -> dict:
    """Move a message to another folder in the same mailbox."""
    message = owned_email(db, user.id, email_id)
    account = _writable_account(db, user, message)
    placement = writable_placement(db, message)
    _rate_limit(user.id)
    if placement.folder == folder:
        return {"success": True, "unchanged": True, "folder": folder}

    async def operation(client):
        names = {item["name"] for item in await list_folders(client)}
        if folder not in names:
            raise HTTPException(
                status_code=404,
                detail=f"No folder named {folder!r} in this mailbox. Call list_mail_folders first.",
            )
        return await move_message(client, placement.folder, placement.uid, folder)

    new_uid = await _with_mailbox(db, account, operation)

    db.delete(placement)
    if new_uid is not None:
        upsert_placement(db, message.id, folder, new_uid, placement.uid_validity)
    # Otherwise leave it unplaced: a message with no placement is never judged
    # deleted, and the next sync pass files it where it now lives.
    db.commit()
    return {
        "success": True,
        "from": placement.folder,
        "to": folder,
        "uid_known": new_uid is not None,
    }


async def delete_mail(db: Session, user: User, *, email_id: int, permanent: bool = False) -> dict:
    """Move a message to Trash, or remove it outright if it is already there.

    Same rule a mail client uses: deleting from anywhere means Trash, and only
    deleting from Trash destroys anything.
    """
    message = owned_email(db, user.id, email_id)
    account = _writable_account(db, user, message)
    placement = writable_placement(db, message)
    _rate_limit(user.id)
    suffixes = settings.excluded_folder_suffixes
    in_trash = is_excluded_folder(placement.folder, suffixes)

    if permanent and not in_trash:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This message is in {placement.folder!r}, not Trash. Delete it first, "
                "then delete it from Trash to remove it permanently."
            ),
        )

    async def operation(client):
        folders = await list_folders(client)
        if permanent:
            await store_flags(
                client, placement.folder, [placement.uid], add=["\\Deleted"], remove=[]
            )
            capabilities = {
                item.upper() for item in getattr(client.client.protocol, "capabilities", set())
            }
            if "UIDPLUS" in capabilities:
                await client.client.uid("expunge", str(placement.uid))
            return None
        trash = _role_folder(folders, "trash", suffixes)
        if not trash:
            raise HTTPException(
                status_code=409,
                detail="This mailbox has no Trash folder; move the message explicitly instead",
            )
        return trash, await move_message(client, placement.folder, placement.uid, trash)

    result = await _with_mailbox(db, account, operation)

    if permanent:
        db.delete(placement)
        # Tombstone rather than delete the row: the grace period is what makes a
        # mistaken deletion recoverable from the index.
        message.deleted_at = datetime.now(tz=timezone.utc)
        db.commit()
        return {"success": True, "permanent": True, "from": placement.folder}

    trash, new_uid = result
    source = placement.folder
    db.delete(placement)
    if new_uid is not None:
        upsert_placement(db, message.id, trash, new_uid, placement.uid_validity)
    db.commit()
    return {"success": True, "permanent": False, "from": source, "to": trash}


async def save_draft(
    db: Session,
    user: User,
    *,
    account_id: int,
    to_addresses: list[str],
    subject: str,
    body_text: str = "",
    body_html: str = "",
    cc_addresses: list[str] | None = None,
    reply_to_email_id: int | None = None,
) -> dict:
    """Store a draft in the mailbox, where any mail client will pick it up."""
    account = owned_account(db, user.id, account_id)
    if account.provider == "gmail" and account.auth_type == "oauth2":
        raise HTTPException(status_code=501, detail="Gmail API accounts cannot append drafts yet")
    if not account.credential_ciphertext:
        raise HTTPException(status_code=400, detail="Mailbox password is not configured")
    _rate_limit(user.id)

    headers = {}
    if reply_to_email_id is not None:
        parent = owned_email(db, user.id, reply_to_email_id)
        if parent.smtp_config_id != account_id:
            raise HTTPException(
                status_code=400,
                detail="A reply draft must use the mailbox that owns the original message",
            )
        if parent.message_id:
            headers["In-Reply-To"] = parent.message_id
            references = (parent.references or "").split()
            if parent.message_id not in references:
                references.append(parent.message_id)
            headers["References"] = " ".join(references[-100:])

    raw = build_draft(
        sender=account.account_name or account.username,
        to_addresses=to_addresses,
        cc_addresses=cc_addresses or [],
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        headers=headers,
    )

    async def operation(client):
        folders = await list_folders(client)
        drafts = next((f["name"] for f in folders if f["special_use"] == "drafts"), None)
        if not drafts:
            drafts = next(
                (f["name"] for f in folders if f["name"].lower().endswith("drafts")), None
            )
        if not drafts:
            raise HTTPException(status_code=409, detail="This mailbox has no Drafts folder")
        return drafts, await append_message(client, drafts, raw, flags=["\\Draft", "\\Seen"])

    drafts, uid = await _with_mailbox(db, account, operation)
    return {"success": True, "folder": drafts, "uid": uid, "bytes": len(raw)}
