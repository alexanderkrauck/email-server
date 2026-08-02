"""A write must refuse rather than guess, and must address the live copy."""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.email.email_processor import acquire_mailbox_lease, mailbox_key, release_mailbox_lease
from src.email.imap_writer import parse_store_response
from src.models.base import Base
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.write_service import mark_mail, writable_placement


def _account(session, account_id, owner, username="a@example.com", host="imap.example.com"):
    session.add(
        SMTPConfig(
            id=account_id,
            owner_user_id=owner,
            provider="imap",
            auth_type="password",
            name=f"Account {account_id}",
            account_name=username,
            host=host,
            port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username=username,
            credential_ciphertext="x",
        )
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, google_sub="owner", email="owner@example.com"))
    session.add(User(id=2, google_sub="other", email="other@example.com"))
    _account(session, 1, owner=1)
    session.flush()
    message = EmailLog(
        id=100,
        smtp_config_id=1,
        sender="a@example.com",
        recipient="b@example.com",
        subject="Invoice",
        provider_message_id="<m@x>",
        message_id="<m@x>",
        email_date=datetime(2025, 3, 4, tzinfo=timezone.utc),
    )
    session.add(message)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _user(db):
    return db.query(User).filter(User.id == 1).one()


def test_the_live_copy_is_addressed_not_the_trashed_one(db):
    """Marking a message read must not act on the copy sitting in Trash."""
    db.add_all(
        [
            MessagePlacement(email_log_id=100, folder="INBOX.Trash", uid=5, uid_validity=1),
            MessagePlacement(email_log_id=100, folder="INBOX", uid=9, uid_validity=1),
        ]
    )
    db.commit()

    assert writable_placement(db, db.get(EmailLog, 100)).folder == "INBOX"


def test_an_unaddressable_placement_loses_to_an_addressable_one(db):
    db.add_all(
        [
            MessagePlacement(email_log_id=100, folder="INBOX", uid=None, uid_validity=1),
            MessagePlacement(email_log_id=100, folder="INBOX.Archive", uid=9, uid_validity=1),
        ]
    )
    db.commit()

    assert writable_placement(db, db.get(EmailLog, 100)).folder == "INBOX.Archive"


def test_a_message_with_no_placement_cannot_be_written(db):
    with pytest.raises(HTTPException) as raised:
        writable_placement(db, db.get(EmailLog, 100))

    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_writes_are_refused_unless_the_operator_enabled_them(db, monkeypatch):
    monkeypatch.setattr(settings, "mail_write_enabled", False)
    db.add(MessagePlacement(email_log_id=100, folder="INBOX", uid=9, uid_validity=1))
    db.commit()

    with pytest.raises(HTTPException) as raised:
        await mark_mail(db, _user(db), email_id=100, mark="read")

    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_mark_is_rejected_before_anything_is_opened(db, monkeypatch):
    monkeypatch.setattr(settings, "mail_write_enabled", True)

    with pytest.raises(HTTPException):
        await mark_mail(db, _user(db), email_id=100, mark="archived")


@pytest.mark.asyncio
async def test_a_write_refuses_while_the_mailbox_is_being_synchronised(db, monkeypatch):
    monkeypatch.setattr(settings, "mail_write_enabled", True)
    db.add(MessagePlacement(email_log_id=100, folder="INBOX", uid=9, uid_validity=1))
    db.commit()
    held = acquire_mailbox_lease(db, db.get(SMTPConfig, 1), seconds=60)
    assert held

    with pytest.raises(HTTPException) as raised:
        await mark_mail(db, _user(db), email_id=100, mark="read")

    assert raised.value.status_code == 409
    release_mailbox_lease(db, held)


def test_a_lease_covers_every_account_naming_the_same_mailbox(db):
    """Two rows can be one physical mailbox, under different owners."""
    _account(db, 2, owner=2)
    db.commit()
    first, second = db.get(SMTPConfig, 1), db.get(SMTPConfig, 2)
    assert mailbox_key(first) == mailbox_key(second)

    token = acquire_mailbox_lease(db, first, seconds=60)
    assert token
    # The second row names the same mailbox, so it must not be acquirable.
    assert acquire_mailbox_lease(db, second, seconds=60) is None

    release_mailbox_lease(db, token)
    assert acquire_mailbox_lease(db, second, seconds=60)


def test_a_separate_mailbox_is_not_blocked(db):
    _account(db, 3, owner=1, username="c@example.com", host="other.example.com")
    db.commit()

    token = acquire_mailbox_lease(db, db.get(SMTPConfig, 1), seconds=60)
    assert token
    assert acquire_mailbox_lease(db, db.get(SMTPConfig, 3), seconds=60)


def test_batched_store_pairs_each_uid_with_its_own_flags():
    """Reading the first FLAGS across all lines writes one result onto all of them."""
    lines = [
        rb"1 FETCH (UID 9 FLAGS (\Seen))",
        rb"2 FETCH (UID 10 FLAGS (\Seen \Flagged))",
        rb"3 FETCH (FLAGS () UID 11)",
        b"STORE completed",
    ]

    assert parse_store_response(lines) == {
        9: r"\Seen",
        10: r"\Seen \Flagged",
        11: "",
    }
