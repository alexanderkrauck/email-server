"""Locate messages stored before this server tracked folders.

A message synced by an early version has no MessagePlacement, no folder, no UID
and no flags. Folder scoping and read-state filters are both blind to it: search
keeps it, because absence of a placement is not evidence of being in Trash, but
it cannot be scoped, filtered or moved. On the deployment this was written for
that is 22,686 of 52,779 messages.

Those rows do carry an RFC Message-ID, which travels with the message, so they
can be found again by sweeping each folder's headers and joining on it.

Read-only by default. --apply writes, and prints the statements that undo it.

    python -m scripts.repair_placements --dry-run
    python -m scripts.repair_placements --dry-run --account 3
    python -m scripts.repair_placements --apply --account 3

Two things this deliberately does not do. It never runs inside the sync loop:
the lease is already held there, and a second acquisition returns None rather
than blocking. And it never guesses -- a message it cannot find upstream is
reported, not deleted.
"""

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes, policy

from sqlalchemy import bindparam, func, update

from src.database.connection import SessionLocal
from src.email.message_flags import normalize_flags
from src.email.smtp_client import SMTPClient
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.services.mail_service import is_excluded_folder

logger = logging.getLogger("repair_placements")

# The RFC Message-ID as stored: normalised, angle brackets included.
MESSAGE_ID = re.compile(rb"<[^<>\r\n]{1,512}>")
HEADER_FETCH = "(UID FLAGS BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
BATCH = 200


@dataclass
class AccountPlan:
    account_id: int
    name: str
    blind: int = 0
    matched: dict[int, list[tuple[str, int, int, str | None]]] = field(default_factory=dict)
    unmatched: list[int] = field(default_factory=list)
    excluded_only: list[int] = field(default_factory=list)
    folders_seen: int = 0
    folders_failed: list[str] = field(default_factory=list)


def _header_message_id(raw: bytes) -> str | None:
    """Extract the Message-ID from raw header bytes.

    Deliberately not email.policy.default: its MessageIDHeader parser truncates
    at a comma in the local part, which silently rewrites real ids.
    """
    match = MESSAGE_ID.search(raw)
    if match:
        return match.group(0).decode("ascii", errors="ignore").strip()
    # Fall back to a lenient parse for headers that arrive folded oddly.
    try:
        parsed = message_from_bytes(raw, policy=policy.compat32)
    except Exception:
        return None
    value = (parsed.get("Message-ID") or "").strip()
    return value or None


def _lines(response) -> list[bytes]:
    # aioimaplib delivers literals as bytearray; every extractor in smtp_client
    # except _extract_raw_email filters on bytes and drops them.
    return [bytes(line) for line in response.lines if isinstance(line, (bytes, bytearray))]


async def _sweep_folder(client: SMTPClient, folder: str) -> dict[str, tuple[int, int, str | None]]:
    """Return {message_id: (uid, uid_validity, flags)} for one folder."""
    selected = await client.client.select(f'"{folder}"')
    if selected.result != "OK":
        raise RuntimeError(f"cannot select {folder}")
    uid_validity = client._extract_uid_validity(selected.lines)

    search = await client.client.search("ALL")
    if search.result != "OK":
        raise RuntimeError(f"cannot search {folder}")
    sequence_ids = [
        token
        for line in _lines(search)
        for token in line.decode("ascii", errors="ignore").split()
        if token.isdigit()
    ]

    found: dict[str, tuple[int, int, str | None]] = {}
    for start in range(0, len(sequence_ids), BATCH):
        chunk = sequence_ids[start : start + BATCH]
        sequence_set = chunk[0] if len(chunk) == 1 else f"{chunk[0]}:{chunk[-1]}"
        response = await client.client.fetch(sequence_set, HEADER_FETCH)
        if response.result != "OK":
            raise RuntimeError(f"cannot fetch headers in {folder}")
        pending_uid: int | None = None
        pending_flags: str | None = None
        for line in _lines(response):
            uid = client._extract_message_uid([line])
            if uid is not None:
                pending_uid = uid
                pending_flags = client._extract_flags([line])
            identity = _header_message_id(line)
            if identity and pending_uid is not None:
                found[identity] = (pending_uid, uid_validity, pending_flags)
    return found


async def plan_account(account: SMTPConfig, blind_rows: dict[str, list[int]]) -> AccountPlan:
    plan = AccountPlan(account_id=account.id, name=account.name, blind=sum(len(v) for v in blind_rows.values()))
    client = SMTPClient(account)
    if not await client.connect():
        raise RuntimeError(f"cannot connect to {account.name}")
    try:
        upstream: dict[str, list[tuple[str, int, int, str | None]]] = {}
        for folder in await client._get_folders():
            try:
                for identity, (uid, validity, flags) in (await _sweep_folder(client, folder)).items():
                    upstream.setdefault(identity, []).append((folder, uid, validity, flags))
                plan.folders_seen += 1
            except Exception as exc:
                # A folder that will not sweep is as invisible as one that came
                # back short. Record it; do not treat its absence as evidence.
                logger.warning("skipping folder %s on %s: %s", folder, account.name, exc)
                plan.folders_failed.append(folder)
    finally:
        await client.disconnect()

    from src.config import settings

    for identity, ids in blind_rows.items():
        locations = upstream.get(identity)
        if not locations:
            plan.unmatched.extend(ids)
            continue
        for email_log_id in ids:
            plan.matched[email_log_id] = locations
            if all(is_excluded_folder(f, settings.excluded_folder_suffixes) for f, _, _, _ in locations):
                plan.excluded_only.append(email_log_id)
    return plan


def blind_rows(db, account_id: int) -> dict[str, list[int]]:
    """Message-ID -> row ids, for rows with no placement and no folder."""
    rows = (
        db.query(EmailLog.id, EmailLog.message_id)
        .filter(
            EmailLog.smtp_config_id == account_id,
            EmailLog.deleted_at.is_(None),
            EmailLog.folder.is_(None),
            ~EmailLog.id.in_(db.query(MessagePlacement.email_log_id)),
        )
        .all()
    )
    grouped: dict[str, list[int]] = {}
    for row in rows:
        identity = (row.message_id or "").strip()
        # A synthetic id was minted locally and cannot match anything upstream.
        if identity.startswith("<"):
            grouped.setdefault(identity, []).append(row.id)
    return grouped


def apply_repair(db, plan: AccountPlan, *, started_at: datetime) -> int:
    """Insert placements and flags for located messages. Returns rows written."""
    from src.config import settings

    written = 0
    flag_groups: dict[str | None, list[int]] = {}
    for email_log_id, locations in plan.matched.items():
        for folder, uid, validity, _flags in locations:
            exists = (
                db.query(MessagePlacement.id)
                .filter(
                    MessagePlacement.email_log_id == email_log_id,
                    MessagePlacement.folder == folder,
                )
                .first()
            )
            if exists:
                continue
            db.add(
                MessagePlacement(
                    email_log_id=email_log_id, folder=folder, uid=uid, uid_validity=validity
                )
            )
            written += 1
        # Flags from the folder the message actually lives in, not from Trash.
        best = sorted(
            locations,
            key=lambda item: (is_excluded_folder(item[0], settings.excluded_folder_suffixes), item[0]),
        )[0]
        flag_groups.setdefault(best[3], []).append(email_log_id)

    for raw, ids in flag_groups.items():
        if raw is None:
            continue
        state = normalize_flags(raw)
        db.execute(
            update(EmailLog.__table__)
            .where(EmailLog.__table__.c.id == bindparam("pk"))
            .values(
                flags=raw,
                is_unread=state.is_unread,
                is_flagged=state.is_flagged,
                is_answered=state.is_answered,
            ),
            [{"pk": email_log_id} for email_log_id in ids],
        )
    db.commit()
    logger.info(
        "rollback if needed:\n"
        "  DELETE FROM message_placements WHERE email_log_id = ANY(ARRAY%s) AND seen_at >= '%s';\n"
        "  UPDATE email_logs SET flags=NULL, is_unread=NULL, is_flagged=NULL, is_answered=NULL "
        "WHERE id = ANY(ARRAY%s);",
        sorted(plan.matched),
        started_at.isoformat(),
        sorted(plan.matched),
    )
    return written


def report(plan: AccountPlan) -> None:
    print(f"\naccount {plan.account_id} ({plan.name})")
    print(f"  blind rows           {plan.blind}")
    print(f"  matched upstream     {len(plan.matched)}")
    print(f"  not found upstream   {len(plan.unmatched)}")
    print(f"  matched ONLY in an excluded folder  {len(plan.excluded_only)}")
    print(f"  folders swept        {plan.folders_seen}")
    if plan.folders_failed:
        print(f"  folders SKIPPED      {plan.folders_failed}")
    if plan.excluded_only:
        print(
            "  note: those messages are visible in default search today only because "
            "they are unplaced. Repairing them removes them from it."
        )


def gate(plan: AccountPlan) -> str | None:
    """Reasons not to write. A message may legitimately be gone upstream."""
    if plan.folders_failed:
        return f"{len(plan.folders_failed)} folder(s) could not be swept"
    if not plan.blind:
        return None
    if len(plan.unmatched) > max(10, plan.blind // 100):
        return (
            f"{len(plan.unmatched)} of {plan.blind} rows were not found upstream, "
            "which is too many to be ordinary deletions"
        )
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write placements (default is read-only)")
    parser.add_argument("--dry-run", action="store_true", help="explicit no-op, the default")
    parser.add_argument("--account", type=int, action="append", help="restrict to one account id")
    parser.add_argument("--force", action="store_true", help="write even if a gate refuses")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    started_at = datetime.now(tz=timezone.utc)
    with SessionLocal() as db:
        query = db.query(SMTPConfig).filter(
            SMTPConfig.enabled,
            SMTPConfig.credential_ciphertext.isnot(None),
            # The Gmail API path never wrote folders, and its provider ids already
            # survive a label change. Sweeping it over IMAP would be pointless.
            ~((SMTPConfig.provider == "gmail") & (SMTPConfig.auth_type == "oauth2")),
        )
        if args.account:
            query = query.filter(SMTPConfig.id.in_(args.account))
        accounts = query.order_by(SMTPConfig.id).all()

        # Only accounts that actually have blind rows: an unscoped sweep opens an
        # IMAP session against another tenant's mailbox for no benefit.
        work = []
        for account in accounts:
            rows = blind_rows(db, account.id)
            if rows:
                work.append((SMTPConfig.create_detached(account), rows))
            else:
                logger.info("account %s has no blind rows", account.id)

    if not work:
        print("nothing to repair")
        return 0

    failed = 0
    for account, rows in work:
        try:
            plan = await plan_account(account, rows)
        except Exception as exc:
            logger.error("account %s failed: %s", account.id, exc)
            failed = 1
            continue
        report(plan)
        refusal = gate(plan)
        if refusal:
            print(f"  REFUSING TO WRITE: {refusal}")
        if not args.apply:
            continue
        if refusal and not args.force:
            failed = 1
            continue
        with SessionLocal() as db:
            written = apply_repair(db, plan, started_at=started_at)
        print(f"  wrote {written} placement(s)")

        # A mis-parsed FLAGS response would mark every repaired row unread and
        # nothing else here would notice.
        with SessionLocal() as db:
            unread = (
                db.query(func.count())
                .select_from(EmailLog)
                .filter(EmailLog.smtp_config_id == account.id, EmailLog.is_unread.is_(True))
                .scalar()
            )
        print(f"  account now reports {unread} unread; compare against the mailbox")
    return failed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
