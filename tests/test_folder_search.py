"""Deleted mail must not rank alongside live mail, and live mail must not vanish."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.base import Base
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import is_excluded_folder, search_mail


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(id=1, google_sub="owner", email="owner@example.com"))
    session.add(
        SMTPConfig(
            id=1,
            owner_user_id=1,
            provider="imap",
            auth_type="password",
            name="Personal",
            account_name="a@example.com",
            host="imap.example.com",
            port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="a@example.com",
        )
    )
    session.flush()

    def add(identity, folders, day):
        message = EmailLog(
            smtp_config_id=1,
            sender="a@example.com",
            recipient="b@example.com",
            subject="Invoice",
            provider_message_id=identity,
            message_id=identity,
            email_date=datetime(2025, 3, day, tzinfo=timezone.utc),
        )
        session.add(message)
        session.flush()
        for index, folder in enumerate(folders, start=1):
            session.add(
                MessagePlacement(
                    email_log_id=message.id, folder=folder, uid=index, uid_validity=1
                )
            )

    add("<live@x>", ["INBOX"], 1)
    add("<trashed@x>", ["INBOX.Trash"], 2)
    # 6,104 production messages look like this after the merge.
    add("<both@x>", ["INBOX", "INBOX.Trash"], 3)
    add("<spam@x>", ["INBOX.SPAM"], 4)
    # 22,686 production rows predate the folder column and have no placement.
    add("<unplaced@x>", [], 5)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _ids(page):
    return {item["message_id"] for item in page["items"]}


def _user(db):
    return db.query(User).one()


def test_a_message_in_both_inbox_and_trash_is_still_found(db):
    """'Exclude if any placement is in Trash' would hide 6,104 real messages."""
    assert "<both@x>" in _ids(search_mail(db, _user(db), deduplicate="none"))


def test_trash_and_spam_are_excluded_by_suffix(db):
    found = _ids(search_mail(db, _user(db), deduplicate="none"))

    assert "<trashed@x>" not in found
    assert "<spam@x>" not in found


def test_a_message_with_no_placement_is_kept(db):
    """Absence of a placement is not evidence of being in Trash."""
    assert "<unplaced@x>" in _ids(search_mail(db, _user(db), deduplicate="none"))


def test_search_can_be_scoped_to_one_folder(db):
    page = search_mail(db, _user(db), folders=["INBOX.Trash"], deduplicate="none")

    assert _ids(page) == {"<trashed@x>", "<both@x>"}


def test_exclusion_can_be_switched_off(db):
    page = search_mail(db, _user(db), exclude_folders=[], deduplicate="none")

    assert page["total_count"] == 5


def test_counts_match_the_filtered_result(db):
    page = search_mail(db, _user(db), deduplicate="none")

    assert page["total_count"] == len(page["items"]) == 3


def test_the_cursor_is_bound_to_the_folder_scope(db):
    """A cursor minted under one scope must not verify under another."""
    from fastapi import HTTPException

    first = search_mail(db, _user(db), exclude_folders=[], limit=1, deduplicate="none")
    assert first["next_cursor"]

    with pytest.raises(HTTPException):
        search_mail(
            db,
            _user(db),
            folders=["INBOX.Trash"],
            cursor=first["next_cursor"],
            limit=1,
            deduplicate="none",
        )


def test_real_provider_trash_names_are_all_matched():
    """Gmail says Bin, Zoho says Deleted Messages, Dovecot says INBOX.Trash."""
    from src.config import settings

    suffixes = settings.excluded_folder_suffixes

    assert is_excluded_folder("[Google Mail]/Bin", suffixes)
    assert is_excluded_folder("[Google Mail]/Spam", suffixes)
    assert is_excluded_folder("Deleted Messages", suffixes)
    assert is_excluded_folder("Papierkorb", suffixes)
    assert not is_excluded_folder("[Google Mail]/All Mail", suffixes)


def test_folder_matching_is_by_suffix_not_exact_name():
    suffixes = ["trash", "spam", "junk"]

    assert is_excluded_folder("INBOX.Trash", suffixes)
    assert is_excluded_folder("[Google Mail]/Trash", suffixes)
    assert is_excluded_folder("INBOX.SPAM", suffixes)
    assert is_excluded_folder("Trash", suffixes)
    assert not is_excluded_folder("INBOX", suffixes)
    assert not is_excluded_folder("INBOX.Projekte.Trashcan-Design", suffixes)
    assert not is_excluded_folder(None, suffixes)


def _codes(page):
    return {warning["code"] for warning in page["warnings"]}


def test_a_folder_scope_warns_about_messages_with_no_known_folder(db):
    page = search_mail(db, _user(db), folders=["INBOX"], deduplicate="none")

    assert "FOLDER_UNKNOWN" in _codes(page)
    assert "<unplaced@x>" not in _ids(page)


def test_the_default_scope_warns_that_unplaced_mail_was_kept(db):
    page = search_mail(db, _user(db), deduplicate="none")

    assert "FOLDER_UNKNOWN" in _codes(page)
    assert "<unplaced@x>" in _ids(page)


def test_no_folder_warning_when_folder_filtering_is_switched_off(db):
    assert "FOLDER_UNKNOWN" not in _codes(
        search_mail(db, _user(db), exclude_folders=[], deduplicate="none")
    )
