"""Tenant ownership is applied to account and message lookup IDs."""

from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.base import Base
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import mail_account_summary, owned_account, owned_email


@pytest.fixture
def tenant_db():
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
        username=name.lower(),
        credential_ciphertext="enc:test",
        imap_use_ssl=True,
        smtp_use_ssl=True,
    )


def test_cross_tenant_ids_are_not_resolvable(tenant_db):
    alice = User(google_sub="alice-sub", email="alice@example.com")
    bob = User(google_sub="bob-sub", email="bob@example.com")
    tenant_db.add_all([alice, bob])
    tenant_db.flush()
    account = _account(alice.id, "Alice")
    tenant_db.add(account)
    tenant_db.flush()
    message = EmailLog(
        smtp_config_id=account.id,
        provider_message_id="INBOX:1:1",
        message_id="<one@example.com>",
        sender="sender@example.com",
        recipient="alice@example.com",
        subject="Private",
    )
    tenant_db.add(message)
    tenant_db.commit()

    assert owned_account(tenant_db, alice.id, account.id).id == account.id
    assert owned_email(tenant_db, alice.id, message.id).id == message.id
    with pytest.raises(HTTPException) as account_error:
        owned_account(tenant_db, bob.id, account.id)
    with pytest.raises(HTTPException) as email_error:
        owned_email(tenant_db, bob.id, message.id)
    assert account_error.value.status_code == 404
    assert email_error.value.status_code == 404


def test_mail_account_summary_has_exact_tenant_scoped_counts(tenant_db):
    alice = User(google_sub="alice-summary", email="alice@example.com")
    bob = User(google_sub="bob-summary", email="bob@example.com")
    tenant_db.add_all([alice, bob])
    tenant_db.flush()
    alice_account = _account(alice.id, "Alice")
    bob_account = _account(bob.id, "Bob")
    tenant_db.add_all([alice_account, bob_account])
    tenant_db.flush()
    tenant_db.add_all(
        [
            EmailLog(
                smtp_config_id=alice_account.id,
                provider_message_id="alice-1",
                message_id="<alice-1@example.com>",
                sender="sender@example.com",
                recipient="alice@example.com",
                attachment_count=2,
            ),
            EmailLog(
                smtp_config_id=alice_account.id,
                provider_message_id="alice-deleted",
                message_id="<alice-deleted@example.com>",
                sender="sender@example.com",
                recipient="alice@example.com",
                attachment_count=1,
                deleted_at=datetime(2026, 7, 28, 12, 0, 0),
            ),
            EmailLog(
                smtp_config_id=bob_account.id,
                provider_message_id="bob-1",
                message_id="<bob-1@example.com>",
                sender="sender@example.com",
                recipient="bob@example.com",
                attachment_count=4,
            ),
        ]
    )
    tenant_db.commit()

    summary = mail_account_summary(tenant_db, alice)

    assert summary["account_count"] == 1
    assert summary["enabled_account_count"] == 1
    assert summary["total_message_count"] == 1
    assert summary["total_attachment_count"] == 2
    assert summary["accounts"][0]["message_count"] == 1
    assert summary["accounts"][0]["attachment_count"] == 2
