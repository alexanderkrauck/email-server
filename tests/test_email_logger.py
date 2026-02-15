"""Tests for email logger."""

import pytest
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

os.environ["EMAIL_LOG_DIR"] = "/tmp/test_email_logger"


@pytest.mark.asyncio
async def test_email_logger_initialization():
    """Test EmailLogger initialization."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = "/tmp/test_emails"
        
        logger = EmailLogger()
        assert logger.log_dir == Path("/tmp/test_emails")


@pytest.mark.asyncio
async def test_log_email_to_file(temp_dir, sample_email_data):
    """Test logging email to file."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(sample_email_data, 1, "test@example.com")
        
        assert result is not None
        assert Path(result).exists()
        assert Path(result).suffix == ".txt"


@pytest.mark.asyncio
async def test_log_email_with_html(temp_dir):
    """Test logging email with HTML content."""
    from src.email.email_logger import EmailLogger

    email_data = {
        "smtp_config_id": 1,
        "sender": "test@example.com",
        "recipient": "recipient@example.com",
        "subject": "Test Email",
        "message_id": "<test-123@example.com>",
        "email_date": datetime(2025, 1, 15, 10, 30, 0),
        "body_plain": "",
        "body_html": "<html><body><p>Hello World</p></body></html>",
        "attachment_count": 0,
    }

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(email_data, 1, "test@example.com")
        
        assert result is not None
        content = Path(result).read_text()
        assert "Hello World" in content


@pytest.mark.asyncio
async def test_log_email_creates_meta_file(temp_dir, sample_email_data):
    """Test that meta file is created alongside text file."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        result = await logger.log_email_to_file(sample_email_data, 1, "test@example.com")
        
        assert result is not None
        txt_path = Path(result)
        meta_path = txt_path.with_suffix(".meta.json")
        
        assert meta_path.exists()
        import json
        meta = json.loads(meta_path.read_text())
        assert meta["id"] == 1
        assert meta["sender"] == "test@example.com"


@pytest.mark.asyncio
async def test_sanitize_filename():
    """Test filename sanitization."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = "/tmp/test"
        
        logger = EmailLogger()
        
        assert logger._sanitize_filename("normal_file.txt") == "normal_file.txt"
        assert logger._sanitize_filename("file<>:.txt") == "file___.txt"
        assert logger._sanitize_filename("") == "unnamed"


@pytest.mark.asyncio
async def test_get_log_files(temp_dir):
    """Test getting log files list."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        
        # Create some test files
        test_file = Path(temp_dir) / "test.txt"
        test_file.write_text("content")
        
        files = logger.get_log_files()
        assert len(files) >= 1
        assert files[0]["name"] == "test.txt"


@pytest.mark.asyncio
async def test_log_attachment_text(temp_dir):
    """Test logging attachment text."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        
        result = await logger.log_attachment_text(
            1, 
            "test@example.com",
            {"filename": "test.txt"},
            "Attachment content"
        )
        
        assert result is not None
        assert Path(result).exists()
        assert "Attachment content" in Path(result).read_text()


@pytest.mark.asyncio
async def test_get_account_directory(temp_dir):
    """Test account directory creation."""
    from src.email.email_logger import EmailLogger

    with patch("src.email.email_logger.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        logger = EmailLogger()
        
        # With account email
        account_dir = logger._get_account_directory("test@example.com", None)
        assert account_dir.name == "test@example.com"
        
        # Without account email, use recipient
        account_dir = logger._get_account_directory(None, "recipient@example.com")
        assert account_dir.name == "recipient@example.com"
