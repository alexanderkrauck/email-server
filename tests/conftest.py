"""Test configuration and fixtures."""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

current_database_url = os.environ.get("EMAILSERVER_DATABASE_URL", "")
if "@postgres:" in current_database_url:
    os.environ["EMAILSERVER_DATABASE_URL"] = current_database_url.rsplit("/", 1)[0] + "/emailserver_test"
else:
    os.environ["EMAILSERVER_DATABASE_URL"] = (
        "postgresql://emailserver:emailserver@localhost:5432/emailserver_test"
    )
os.environ.setdefault("EMAILSERVER_API_HOST", "0.0.0.0")
os.environ.setdefault("EMAILSERVER_API_PORT", "8000")

# Generated signing and encryption material must never touch the deployment's
# /data volume, so the suite runs from a clean clone without Docker.
_test_data_dir = Path(tempfile.mkdtemp(prefix="emailserver-test-data-"))
os.environ.setdefault("EMAILSERVER_DATA_DIR", str(_test_data_dir))

# pydantic-settings reads a real .env before the environment above. A developer's
# private deployment file must not decide what the suite asserts.
os.environ.setdefault("EMAILSERVER_AUTH_MODE", "development")


@pytest.fixture
def sample_email_data():
    """Sample email data for testing."""
    return {
        "smtp_config_id": 1,
        "sender": "test@example.com",
        "recipient": "recipient@example.com",
        "subject": "Test Email",
        "message_id": "<test-123@example.com>",
        "email_date": datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "body_plain": "This is a test email body.",
        "body_html": "<html><body><p>This is a test email body.</p></body></html>",
        "attachment_count": 0,
    }


@pytest.fixture
def sample_email_with_attachments():
    """Sample email data with attachments."""
    return {
        "smtp_config_id": 1,
        "sender": "test@example.com",
        "recipient": "recipient@example.com",
        "subject": "Test Email with Attachments",
        "message_id": "<test-456@example.com>",
        "email_date": datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        "body_plain": "This email has attachments.",
        "body_html": "<html><body><p>This email has attachments.</p></body></html>",
        "attachment_count": 2,
    }


@pytest.fixture
def sample_raw_email():
    """Sample raw email for testing."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email
Date: Wed, 15 Jan 2025 10:30:00 +0000
Message-ID: <test-123@example.com>
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

This is a test email body.
"""


@pytest.fixture
def sample_raw_email_with_attachment():
    """Sample raw email with attachment."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Test Email with Attachment
Date: Wed, 15 Jan 2025 10:30:00 +0000
Message-ID: <test-456@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=boundary123

--boundary123
Content-Type: text/plain; charset=UTF-8

This email has an attachment.

--boundary123
Content-Type: text/plain; filename="test.txt"
Content-Disposition: attachment; filename="test.txt"

This is the attachment content.

--boundary123--
"""


@pytest.fixture
def mock_smtp_config():
    """Mock SMTP configuration."""
    from dataclasses import dataclass

    @dataclass
    class MockSMTPConfig:
        id: int = 1
        name: str = "Test Account"
        account_name: str = "test@example.com"
        host: str = "imap.example.com"
        port: int = 993
        username: str = "testuser"
        password: str = "testpass"
        credential_ciphertext: str = "testpass"
        auth_type: str = "password"
        smtp_host: str = "smtp.example.com"
        smtp_port: int = 587
        enabled: bool = True
        imap_use_ssl: bool = True
        smtp_use_tls: bool = True
        smtp_use_ssl: bool = False
        sync_cursors: dict = None

    return MockSMTPConfig()
