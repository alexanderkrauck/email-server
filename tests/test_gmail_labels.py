"""Gmail has labels, not folders, and the difference has to be projected."""

import pytest

from src.email.gmail_labels import (
    ARCHIVE,
    labels_for_move,
    location_for,
    location_for_stored,
    parse_labels,
)


def test_a_message_reports_exactly_one_location():
    """Two locations would be counted twice and addressed ambiguously."""
    assert location_for(["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]) == "INBOX"
    assert location_for(["SENT"]) == "SENT"
    assert location_for([]) == ARCHIVE


def test_removal_beats_everything_else():
    """A message in Trash is in Trash whatever else is true of it."""
    assert location_for(["INBOX", "TRASH"]) == "TRASH"
    assert location_for(["INBOX", "SPAM"]) == "SPAM"
    assert location_for(["TRASH", "SPAM"]) == "TRASH"


def test_trash_and_spam_names_are_excluded_by_the_ordinary_rule():
    from src.config import settings
    from src.services.mail_service import is_excluded_folder

    suffixes = settings.excluded_folder_suffixes

    assert is_excluded_folder(location_for(["TRASH"]), suffixes)
    assert is_excluded_folder(location_for(["SPAM"]), suffixes)
    assert not is_excluded_folder(location_for(["INBOX"]), suffixes)
    assert not is_excluded_folder(ARCHIVE, suffixes)


def test_a_move_clears_the_location_it_came_from():
    """Adding a label without removing the old one leaves it in both."""
    add, remove = labels_for_move(["INBOX", "UNREAD"], "TRASH")

    assert add == ["TRASH"]
    assert remove == ["INBOX"]


def test_archiving_removes_the_inbox_and_adds_nothing():
    add, remove = labels_for_move(["INBOX", "STARRED"], ARCHIVE)

    assert add == []
    assert remove == ["INBOX"]


def test_labels_gmail_assigns_are_left_alone():
    """SENT and DRAFT are the server's to set; a client cannot remove them."""
    _add, remove = labels_for_move(["SENT", "INBOX"], "TRASH")

    assert "SENT" not in remove


def test_a_move_to_where_it_already_is_changes_nothing():
    assert labels_for_move(["INBOX"], "INBOX") == ([], [])


def test_an_unknown_destination_is_rejected():
    with pytest.raises(ValueError):
        labels_for_move(["INBOX"], "Rechnungen")


def test_stored_flags_are_read_back_as_a_location():
    assert location_for_stored('["CATEGORY_UPDATES", "INBOX", "UNREAD"]') == "INBOX"
    assert location_for_stored(None) == ARCHIVE
    # An IMAP flag string is not a label set and must not be read as one.
    assert parse_labels(r"\Seen \Answered") == []


def test_a_gmail_placement_is_addressable_without_a_uid(tmp_path):
    """Requiring a UID would refuse every write on a Gmail API account."""
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.base import Base
    from src.models.email import EmailLog
    from src.models.placement import MessagePlacement
    from src.services.write_service import writable_placement

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    message = EmailLog(
        id=1,
        smtp_config_id=1,
        sender="a@example.com",
        recipient="b@example.com",
        subject="x",
        provider_message_id="18fabc",
        message_id="<x@y>",
        email_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    db.add(message)
    db.add(MessagePlacement(email_log_id=1, folder="INBOX", uid=None, uid_validity=None))
    db.commit()

    from fastapi import HTTPException

    assert writable_placement(db, message, requires_uid=False).folder == "INBOX"
    with pytest.raises(HTTPException):
        writable_placement(db, message, requires_uid=True)
