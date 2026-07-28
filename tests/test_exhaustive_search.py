"""Exhaustive search contracts: counts, cursors, participants, and threads."""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.base import Base
from src.models.email import EmailLog
from src.models.participant import MailParticipant
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import (
    _database_regex_pattern,
    get_thread,
    search_mail,
)


@pytest.fixture
def search_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _account(owner_id: int, name: str) -> SMTPConfig:
    return SMTPConfig(
        owner_user_id=owner_id,
        provider="imap",
        auth_type="password",
        name=name,
        account_name=f"{name.lower()}@example.com",
        host="imap.example.com",
        port=993,
        smtp_host="smtp.example.com",
        smtp_port=465,
        username=f"{name.lower()}@example.com",
        credential_ciphertext="enc:test",
        imap_use_ssl=True,
        smtp_use_ssl=True,
        enabled=True,
        sync_state="healthy",
        backfill_complete=True,
        last_success_at=datetime(2026, 7, 28, 12, 0, 0),
    )


def _message(account_id: int, index: int, message_id: str) -> EmailLog:
    return EmailLog(
        smtp_config_id=account_id,
        provider_message_id=f"provider-{account_id}-{index}",
        message_id=message_id,
        sender="person@example.com",
        recipient="owner@example.com",
        subject=f"Message {index}",
        body_plain=f"Body {index}",
        email_date=datetime(2026, 7, 28, 12, 0, 0) - timedelta(minutes=index),
    )


def test_search_has_exact_counts_stable_cursor_and_source_preserving_dedup(
    search_db,
):
    owner = User(google_sub="owner", email="owner@example.com")
    search_db.add(owner)
    search_db.flush()
    first = _account(owner.id, "First")
    second = _account(owner.id, "Second")
    search_db.add_all([first, second])
    search_db.flush()
    messages = [
        _message(first.id, 1, "<duplicate@example.com>"),
        _message(second.id, 2, "<duplicate@example.com>"),
        _message(first.id, 3, "<unique-1@example.com>"),
        _message(second.id, 4, "<unique-2@example.com>"),
    ]
    search_db.add_all(messages)
    search_db.commit()

    first_page = search_mail(
        search_db,
        owner,
        limit=2,
        deduplicate="exact",
    )
    second_page = search_mail(
        search_db,
        owner,
        limit=2,
        cursor=first_page["next_cursor"],
        deduplicate="exact",
    )

    assert first_page["raw_count"] == 4
    assert first_page["total_count"] == 3
    assert first_page["has_more"] is True
    assert second_page["has_more"] is False
    assert {
        item["id"] for item in first_page["items"]
    }.isdisjoint({item["id"] for item in second_page["items"]})
    duplicate = next(
        item
        for item in first_page["items"] + second_page["items"]
        if item["duplicate_count"] == 2
    )
    assert {source["account_id"] for source in duplicate["sources"]} == {
        first.id,
        second.id,
    }

    with pytest.raises(HTTPException) as mismatched:
        search_mail(
            search_db,
            owner,
            participant="different@example.com",
            cursor=first_page["next_cursor"],
        )
    assert mismatched.value.status_code == 400


def test_participant_filter_includes_cc_and_returns_domain_facets(search_db):
    owner = User(google_sub="participant-owner", email="owner@example.com")
    search_db.add(owner)
    search_db.flush()
    account = _account(owner.id, "Work")
    search_db.add(account)
    search_db.flush()
    message = _message(account.id, 1, "<hays@example.com>")
    search_db.add(message)
    search_db.flush()
    search_db.add(
        MailParticipant(
            email_log_id=message.id,
            role="cc",
            email="consultant@hays.de",
            domain="hays.de",
        )
    )
    search_db.commit()

    page = search_mail(search_db, owner, participants=["hays.de"])

    assert page["total_count"] == 1
    assert page["items"][0]["matches"] == [
        {
            "field": "participant",
            "role": "cc",
            "email": "consultant@hays.de",
        }
    ]
    assert {"domain": "hays.de", "count": 1} in page["facets"][
        "participant_domains"
    ]


def test_thread_uses_reply_headers_and_defaults_to_bounded_plain_text(search_db):
    owner = User(google_sub="thread-owner", email="owner@example.com")
    search_db.add(owner)
    search_db.flush()
    account = _account(owner.id, "Thread")
    search_db.add(account)
    search_db.flush()
    parent = _message(account.id, 1, "<parent@example.com>")
    parent.body_plain = "Parent plain"
    parent.body_html = "<style>large css</style><p>Parent</p>"
    child = _message(account.id, 2, "<child@example.com>")
    child.in_reply_to = "<parent@example.com>"
    child.references = "<parent@example.com>"
    child.body_plain = "Child plain"
    child.body_html = "<style>large css</style><p>Child</p>"
    search_db.add_all([parent, child])
    search_db.commit()

    thread = get_thread(
        search_db,
        owner,
        child.id,
        body_format="plain",
        max_body_chars=8,
    )

    assert thread["reconstruction_method"] == "reply_headers"
    assert thread["confidence"] == "high"
    assert thread["message_count"] == 2
    assert all("body_html" not in message for message in thread["messages"])
    assert all(len(message["body_plain"]) <= 8 for message in thread["messages"])


def test_postgres_regex_word_boundaries_are_translated_without_touching_literals():
    assert _database_regex_pattern(r"\bword\B", "postgresql") == r"\yword\Y"
    assert _database_regex_pattern(r"\\bword", "postgresql") == r"\\bword"
    assert _database_regex_pattern(r"\bword", "sqlite") == r"\bword"
