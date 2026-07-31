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
    assert attachment.sha256 is not None
    assert attachment.extraction_state == "complete"
    # text_content should be populated for text/plain
    assert attachment.text_content is not None


@pytest.mark.asyncio
async def test_process_attachment_removes_nul_characters():
    """PostgreSQL-incompatible NUL characters are removed from extracted text."""
    from src.email.attachment_handler import AttachmentHandler
    from src.storage_config.resolver import StorageConfig

    handler = AttachmentHandler()
    mock_part = MagicMock()
    mock_part.get_filename.return_value = "nul.txt"
    mock_part.get_content_type.return_value = "text/plain"
    mock_part.get.return_value = ""
    mock_part.get_payload.return_value = b"before\x00after"
    storage_config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1024,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    attachment = await handler._process_attachment(mock_part, 1, storage_config)

    assert attachment.text_content == "beforeafter"


@pytest.mark.asyncio
async def test_oversized_attachment_is_not_parsed():
    from src.email.attachment_handler import AttachmentHandler
    from src.storage_config.resolver import StorageConfig

    handler = AttachmentHandler()
    part = MagicMock()
    part.get_filename.return_value = "large.txt"
    part.get_content_type.return_value = "text/plain"
    part.get.return_value = ""
    part.get_payload.return_value = b"too large"
    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=4,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    attachment = await handler._process_attachment(part, 1, config)

    assert attachment.extraction_state == "skipped_size"
    assert attachment.text_content is None


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


def test_attachment_filename_removes_header_control_characters():
    from src.email import sanitize_filename

    assert sanitize_filename("report\r\n folded.pdf") == "report_folded.pdf"


def test_refetch_prefers_a_live_folder_over_trash():
    """A moved message must stay refetchable; the old UID is gone upstream."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.base import Base
    from src.models.email import EmailLog
    from src.models.placement import MessagePlacement
    from src.services.attachment_service import current_placement

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    message = EmailLog(
        smtp_config_id=1,
        sender="a@example.com",
        recipient="b@example.com",
        provider_message_id="<abc@example.com>",
        message_id="<abc@example.com>",
    )
    db.add(message)
    db.commit()
    db.add_all(
        [
            MessagePlacement(email_log_id=message.id, folder="INBOX.Trash", uid=3, uid_validity=1),
            MessagePlacement(email_log_id=message.id, folder="INBOX.Archive", uid=12, uid_validity=1),
        ]
    )
    db.commit()

    placement = current_placement(db, message.id)

    assert (placement.folder, placement.uid) == ("INBOX.Archive", 12)
    db.close()


def test_refetch_has_no_placement_to_use_for_legacy_rows():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.base import Base
    from src.models.email import EmailLog
    from src.services.attachment_service import current_placement

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    message = EmailLog(
        smtp_config_id=1,
        sender="a@example.com",
        recipient="b@example.com",
        provider_message_id="<abc@example.com>",
        message_id="<abc@example.com>",
    )
    db.add(message)
    db.commit()

    assert current_placement(db, message.id) is None
    db.close()
