"""Account configuration writes remain tenant-scoped and credential-safe."""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.handlers import email_handler
from src.handlers.email_handler import MailAccountCreate, MailAccountUpdate
from src.models.base import Base
from src.models.smtp_config import SMTPConfig
from src.models.user import User


@pytest.fixture
def account_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_create_and_update_never_return_password(account_db, monkeypatch):
    async def connection_ok(account):
        return {"imap": True, "smtp": True}

    monkeypatch.setattr(email_handler, "verify_account_connection", connection_ok)
    user = User(google_sub="owner", email="owner@example.com")
    account_db.add(user)
    account_db.commit()

    created = await email_handler.create_account(
        MailAccountCreate(
            name="Work",
            account_name="owner@example.com",
            provider="imap",
            host="imap.example.com",
            smtp_host="smtp.example.com",
            username="owner@example.com",
            password="initial-secret",
        ),
        user,
        account_db,
    )

    account_id = created["id"]
    account = account_db.get(SMTPConfig, account_id)
    assert account.password == "initial-secret"
    assert "initial-secret" not in str(created)
    assert "password" not in created
    assert "credential_ciphertext" not in created

    updated = await email_handler.update_account(
        account_id,
        MailAccountUpdate(
            host="imap2.example.com",
            password="rotated-secret",
        ),
        user,
        account_db,
    )

    account_db.refresh(account)
    assert account.host == "imap2.example.com"
    assert account.password == "rotated-secret"
    assert "rotated-secret" not in str(updated)
    assert "password" not in updated
    assert "credential_ciphertext" not in updated


@pytest.mark.asyncio
async def test_failed_update_rolls_back_and_cross_tenant_update_is_hidden(
    account_db,
    monkeypatch,
):
    async def connection_failed(account):
        return {"imap": False, "smtp": False}

    owner = User(google_sub="owner", email="owner@example.com")
    other = User(google_sub="other", email="other@example.com")
    account_db.add_all([owner, other])
    account_db.flush()
    account = SMTPConfig(
        owner_user_id=owner.id,
        provider="imap",
        auth_type="password",
        name="Work",
        account_name="owner@example.com",
        host="imap.example.com",
        port=993,
        smtp_host="smtp.example.com",
        smtp_port=465,
        username="owner@example.com",
        imap_use_ssl=True,
        imap_use_tls=False,
        smtp_use_ssl=True,
        smtp_use_tls=False,
        enabled=True,
    )
    account.password = "working-secret"
    account_db.add(account)
    account_db.commit()
    account_id = account.id
    monkeypatch.setattr(email_handler, "verify_account_connection", connection_failed)

    with pytest.raises(HTTPException) as failed:
        await email_handler.update_account(
            account_id,
            MailAccountUpdate(host="broken.example.com", password="bad-secret"),
            owner,
            account_db,
        )
    assert failed.value.status_code == 400
    account_db.refresh(account)
    assert account.host == "imap.example.com"
    assert account.password == "working-secret"

    with pytest.raises(HTTPException) as hidden:
        await email_handler.update_account(
            account_id,
            MailAccountUpdate(name="Stolen", verify_connection=False),
            other,
            account_db,
        )
    assert hidden.value.status_code == 404


def test_dashboard_route_is_removed():
    from src.server import final_app

    assert all(getattr(route, "path", None) != "/dashboard" for route in final_app.routes)
