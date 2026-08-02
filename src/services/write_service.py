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
from sqlalchemy.orm import Session

from src.config import settings
from src.email.email_processor import acquire_mailbox_lease, release_mailbox_lease
from src.email.imap_writer import ImapWriteError, store_flags
from src.email.message_flags import apply_flag_state
from src.email.smtp_client import SMTPClient
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import is_excluded_folder, owned_email, serialize_email

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
    if not settings.mail_write_enabled:
        raise HTTPException(status_code=403, detail="Mailbox writes are disabled on this server")
    allowed = settings.mail_write_allowed_account_ids
    if allowed and account.id not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Mailbox writes are not enabled for account {account.id}",
        )
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
