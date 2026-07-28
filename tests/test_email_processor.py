"""Durable synchronization cursor behavior."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.email import email_processor
from src.email.email_processor import EmailProcessor
from src.models.base import Base
from src.models.smtp_config import SMTPConfig
from src.models.sync_cursor import MailSyncCursor
from src.models.user import User


def test_uidvalidity_change_replaces_instead_of_maximizing_cursor(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        owner = User(google_sub="cursor-owner", email="owner@example.com")
        db.add(owner)
        db.flush()
        account = SMTPConfig(
            owner_user_id=owner.id,
            provider="imap",
            auth_type="password",
            name="Cursor",
            account_name="owner@example.com",
            host="imap.example.com",
            port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="owner@example.com",
            credential_ciphertext="enc:test",
        )
        db.add(account)
        db.flush()
        account_id = account.id
        db.add(
            MailSyncCursor(
                smtp_config_id=account_id,
                folder="INBOX",
                uid_validity=100,
                last_uid=900,
                backfill_complete=True,
            )
        )

    @contextmanager
    def local_session():
        db = sessions()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(email_processor, "get_db_session", local_session)
    processor = EmailProcessor()
    processor._persist_sync_cursors(
        [
            {
                "smtp_config_id": account_id,
                "folder": "INBOX",
                "imap_uid": 4,
                "uid_validity": 200,
            }
        ]
    )
    with sessions() as db:
        cursor = db.query(MailSyncCursor).one()
        assert cursor.uid_validity == 200
        assert cursor.last_uid == 4
        assert cursor.backfill_complete is False
        cursor.uid_validity = 300
        cursor.last_uid = 800
        cursor.backfill_complete = True
        db.commit()

    client = SimpleNamespace(
        _last_uids={"INBOX": 7},
        _uid_validities={"INBOX": 400},
        _folder_backfill_complete={"INBOX": False},
    )
    processor._persist_client_cursors(account_id, client)
    with sessions() as db:
        cursor = db.query(MailSyncCursor).one()
        assert cursor.uid_validity == 400
        assert cursor.last_uid == 7
        assert cursor.backfill_complete is False


@pytest.mark.asyncio
async def test_recent_reconciliation_checkpoint_survives_worker_restart(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        owner = User(google_sub="reconcile-owner", email="owner@example.com")
        db.add(owner)
        db.flush()
        account = SMTPConfig(
            owner_user_id=owner.id,
            provider="imap",
            auth_type="password",
            name="Reconcile",
            account_name="owner@example.com",
            host="imap.example.com",
            port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="owner@example.com",
            credential_ciphertext="enc:test",
            backfill_complete=True,
            last_reconciled_at=datetime.now(tz=timezone.utc),
        )
        db.add(account)
        db.flush()
        account_id = account.id

    @contextmanager
    def local_session():
        db = sessions()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(email_processor, "get_db_session", local_session)
    client = SimpleNamespace(fetch_folder_state=AsyncMock())

    await EmailProcessor()._reconcile_provider_state(account_id, client)

    client.fetch_folder_state.assert_not_awaited()


def test_shutdown_interruption_does_not_leave_account_in_error(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        owner = User(google_sub="shutdown-owner", email="owner@example.com")
        db.add(owner)
        db.flush()
        account = SMTPConfig(
            owner_user_id=owner.id,
            provider="imap",
            auth_type="password",
            name="Shutdown",
            account_name="owner@example.com",
            host="imap.example.com",
            port=993,
            smtp_host="smtp.example.com",
            smtp_port=465,
            username="owner@example.com",
            credential_ciphertext="enc:test",
            sync_state="syncing",
            backfill_complete=True,
        )
        db.add(account)
        db.flush()
        account_id = account.id

    monkeypatch.setattr(email_processor, "SessionLocal", sessions)
    EmailProcessor._mark_sync_interrupted(account_id)

    with sessions() as db:
        account = db.get(SMTPConfig, account_id)
        assert account.sync_state == "healthy"
        assert account.last_error_code is None
        assert account.retry_at is None
