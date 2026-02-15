"""Tests for attachment handler."""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ["EMAIL_LOG_DIR"] = "/tmp/test_attachments"


def test_attachment_handler_initialization():
    """Test AttachmentHandler initialization."""
    from src.email.attachment_handler import AttachmentHandler

    with patch("src.email.attachment_handler.settings") as mock_settings:
        mock_settings.email_log_dir = "/tmp/test"
        
        handler = AttachmentHandler()
        assert handler.email_log_dir == Path("/tmp/test")


def test_is_attachment_with_filename():
    """Test _is_attachment returns True for attachment with filename."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    mock_part = MagicMock()
    mock_part.get_filename.return_value = "document.pdf"
    mock_part.get.return_value = ""
    mock_part.get_content_type.return_value = "application/pdf"
    
    assert handler._is_attachment(mock_part) is True


def test_is_attachment_with_content_disposition():
    """Test _is_attachment with Content-Disposition header."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    mock_part = MagicMock()
    mock_part.get_filename.return_value = None
    mock_part.get.return_value = "attachment; filename=test.txt"
    mock_part.get_content_type.return_value = "text/plain"
    
    assert handler._is_attachment(mock_part) is True


def test_is_attachment_inline_image():
    """Test _is_attachment for inline images."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    mock_part = MagicMock()
    mock_part.get_filename.return_value = None
    mock_part.get.return_value = ""
    mock_part.get_content_type.return_value = "image/png"
    
    assert handler._is_attachment(mock_part) is True


def test_is_attachment_not_attachment():
    """Test _is_attachment returns False for non-attachments."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    mock_part = MagicMock()
    mock_part.get_filename.return_value = None
    mock_part.get.return_value = ""
    mock_part.get_content_type.return_value = "text/plain"
    
    assert handler._is_attachment(mock_part) is False


def test_format_file_size():
    """Test file size formatting."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    assert handler._format_file_size(500) == "500 B"
    assert handler._format_file_size(1024) == "1.0 KB"
    assert handler._format_file_size(1048576) == "1.0 MB"
    assert handler._format_file_size(1073741824) == "1.0 GB"


@pytest.mark.asyncio
async def test_save_attachment_text(temp_dir):
    """Test saving attachment text to file."""
    from src.email.attachment_handler import AttachmentHandler

    with patch("src.email.attachment_handler.settings") as mock_settings:
        mock_settings.email_log_dir = temp_dir
        
        handler = AttachmentHandler()
        
        result = await handler._save_attachment_text(
            "Attachment content",
            "test.txt",
            1,
            "test@example.com"
        )
        
        assert result is not None
        assert Path(result).exists()
        assert Path(result).read_text() == "Attachment content"


@pytest.mark.asyncio
async def test_extract_attachments_empty_email():
    """Test extracting attachments from email with no attachments."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    
    raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test
MIME-Version: 1.0
Content-Type: text/plain

Simple email body.
"""
    
    with patch("src.email.attachment_handler.settings") as mock_settings:
        mock_settings.email_log_dir = "/tmp/test"
        
        attachments = await handler.extract_attachments(raw_email, 1, "test@example.com", None)
    
    assert len(attachments) == 0
