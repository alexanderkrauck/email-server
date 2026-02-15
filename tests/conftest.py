"""Test configuration and fixtures."""

import pytest
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///test_emailserver.db"
os.environ["EMAIL_LOG_DIR"] = "/tmp/test_emails"
os.environ["EMAILSERVER_API_HOST"] = "0.0.0.0"
os.environ["EMAILSERVER_API_PORT"] = "8000"


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_email_data():
    """Sample email data for testing."""
    return {
        "smtp_config_id": 1,
        "sender": "test@example.com",
        "recipient": "recipient@example.com",
        "subject": "Test Email",
        "message_id": "<test-123@example.com>",
        "email_date": datetime(2025, 1, 15, 10, 30, 0),
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
        "email_date": datetime(2025, 1, 15, 10, 30, 0),
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
        smtp_host: str = "smtp.example.com"
        smtp_port: int = 587
        enabled: bool = True
        imap_use_ssl: bool = True
        smtp_use_tls: bool = True

    return MockSMTPConfig()
