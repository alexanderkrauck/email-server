"""Unknown read state must never be reported as read, or silently dropped."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.base import Base
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import search_mail, search_mail_regex


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

    def add(identity, day, **state):
        session.add(
            EmailLog(
                smtp_config_id=1,
                sender="a@example.com",
                recipient="b@example.com",
                subject="Invoice",
                body_plain="body",
                provider_message_id=identity,
                message_id=identity,
                email_date=datetime(2025, 3, day, tzinfo=timezone.utc),
                **state,
            )
        )

    add("<unread@x>", 1, is_unread=True, is_flagged=False, is_answered=False)
    add("<read@x>", 2, is_unread=False, is_flagged=False, is_answered=True)
    add("<starred@x>", 3, is_unread=False, is_flagged=True, is_answered=False)
    # 22,686 production rows were stored before flags were ever fetched.
    add("<unknown@x>", 4)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _ids(page):
    return {item["message_id"] for item in page["items"]}


def _user(db):
    return db.query(User).one()


def _codes(page):
    return {warning["code"] for warning in page["warnings"]}


def test_unfiltered_search_returns_every_message(db):
    assert search_mail(db, _user(db), deduplicate="none")["total_count"] == 4


def test_unread_filter_selects_only_unread(db):
    page = search_mail(db, _user(db), is_unread=True, deduplicate="none")

    assert _ids(page) == {"<unread@x>"}


def test_read_filter_does_not_claim_unknown_messages_were_read(db):
    """is_unread=False must not sweep in every message nothing is known about."""
    page = search_mail(db, _user(db), is_unread=False, deduplicate="none")

    assert _ids(page) == {"<read@x>", "<starred@x>"}


def test_filtering_on_flags_warns_about_what_it_could_not_judge(db):
    page = search_mail(db, _user(db), is_unread=True, deduplicate="none")

    assert "FLAG_STATE_UNKNOWN" in _codes(page)
    assert "1 message(s)" in next(
        warning["message"]
        for warning in page["warnings"]
        if warning["code"] == "FLAG_STATE_UNKNOWN"
    )


def test_no_flag_warning_when_no_flag_filter_is_used(db):
    assert "FLAG_STATE_UNKNOWN" not in _codes(search_mail(db, _user(db), deduplicate="none"))


def test_flagged_and_unanswered_compose(db):
    page = search_mail(db, _user(db), is_flagged=True, is_answered=False, deduplicate="none")

    assert _ids(page) == {"<starred@x>"}


def test_counts_match_the_filtered_result(db):
    page = search_mail(db, _user(db), is_unread=False, deduplicate="none")

    assert page["total_count"] == page["raw_count"] == len(page["items"]) == 2


def test_the_cursor_is_bound_to_the_flag_filter(db):
    """A cursor minted over all mail must not verify against unread-only."""
    from fastapi import HTTPException

    first = search_mail(db, _user(db), limit=1, deduplicate="none")
    assert first["next_cursor"]

    with pytest.raises(HTTPException):
        search_mail(
            db,
            _user(db),
            is_unread=True,
            cursor=first["next_cursor"],
            limit=1,
            deduplicate="none",
        )


def test_regex_search_takes_the_same_filters():
    """Regex search needs PostgreSQL to run, so check the wiring, not the rows."""
    import inspect

    signature = inspect.signature(search_mail_regex).parameters
    source = inspect.getsource(search_mail_regex)

    assert {"is_unread", "is_flagged", "is_answered"} <= set(signature)
    assert "_apply_flag_filters(q, flag_filters)" in source
    assert "_flag_state_warning(scope_before_flags, flag_filters)" in source


def test_flag_state_is_reported_on_every_result(db):
    page = search_mail(db, _user(db), deduplicate="none")
    by_id = {item["message_id"]: item for item in page["items"]}

    assert by_id["<unread@x>"]["is_unread"] is True
    assert by_id["<read@x>"]["is_answered"] is True
    # Null, not false: nothing was ever observed about this message.
    assert by_id["<unknown@x>"]["is_unread"] is None
