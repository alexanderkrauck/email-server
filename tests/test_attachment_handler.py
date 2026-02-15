"""Tests for attachment handler."""

from unittest.mock import MagicMock

import pytest


def test_attachment_handler_initialization():
    """Test AttachmentHandler can be instantiated."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()
    assert handler is not None


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

    attachments = await handler.extract_attachments(raw_email, 1, None)

    assert len(attachments) == 0


@pytest.mark.asyncio
async def test_process_attachment_creates_db_object():
    """Test that _process_attachment returns an EmailAttachment with text_content."""
    from src.email.attachment_handler import AttachmentHandler
    from src.storage_config.resolver import StorageConfig

    handler = AttachmentHandler()

    mock_part = MagicMock()
    mock_part.get_filename.return_value = "test.txt"
    mock_part.get_content_type.return_value = "text/plain"
    mock_part.get.return_value = ""
    mock_part.get_payload.return_value = b"Hello, this is test content."

    storage_config = StorageConfig(
        store_text_only=False,
        max_attachment_size=10 * 1024 * 1024,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    attachment = await handler._process_attachment(mock_part, 1, storage_config)

    assert attachment is not None
    assert attachment.filename == "test.txt"
    assert attachment.content_type == "text/plain"
    assert attachment.size == len(b"Hello, this is test content.")
    assert attachment.email_log_id == 1
    # text_content should be populated for text/plain
    assert attachment.text_content is not None


@pytest.mark.asyncio
async def test_extract_attachments_with_attachment():
    """Test extracting attachments from email with an attachment."""
    from src.email.attachment_handler import AttachmentHandler

    handler = AttachmentHandler()

    raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test with Attachment
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

    attachments = await handler.extract_attachments(raw_email, 1, None)

    assert len(attachments) == 1
    assert attachments[0].filename == "test.txt"
    assert attachments[0].content_type == "text/plain"
    # Text content stored in DB column, not on filesystem
    assert attachments[0].text_content is not None
    assert "attachment content" in attachments[0].text_content
