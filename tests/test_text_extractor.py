"""Tests for text extractor."""

import pytest
from unittest.mock import patch, MagicMock


def test_text_extractor_initialization():
    """Test TextExtractor initialization."""
    from src.email.text_extractor import TextExtractor

    extractor = TextExtractor()
    assert extractor is not None


@pytest.mark.asyncio
async def test_extract_plain_text():
    """Test extracting plain text."""
    from src.email.text_extractor import TextExtractor
    from src.storage_config.resolver import StorageConfig

    extractor = TextExtractor()
    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    data = b"Hello, World!"
    result = await extractor.extract(data, "text/plain", config)
    
    assert result == "Hello, World!"


@pytest.mark.asyncio
async def test_extract_html():
    """Test extracting text from HTML."""
    from src.email.text_extractor import TextExtractor
    from src.storage_config.resolver import StorageConfig

    extractor = TextExtractor()
    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    data = b"<html><body><p>Hello World</p></body></html>"
    result = await extractor.extract(data, "text/html", config)
    
    assert "Hello World" in result


@pytest.mark.asyncio
async def test_extract_unsupported_type():
    """Test extracting unsupported content type."""
    from src.email.text_extractor import TextExtractor
    from src.storage_config.resolver import StorageConfig

    extractor = TextExtractor()
    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=False,
        extract_docx_text=False,
        extract_image_text=False,
        extract_other_text=False,
    )

    data = b"some binary data"
    result = await extractor.extract(data, "application/octet-stream", config)
    
    assert result is None


@pytest.mark.asyncio
async def test_extract_with_disabled_config():
    """Test extraction is disabled via config."""
    from src.email.text_extractor import TextExtractor
    from src.storage_config.resolver import StorageConfig

    extractor = TextExtractor()
    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=False,
        extract_docx_text=False,
        extract_image_text=False,
        extract_other_text=False,
    )

    data = b"<html><body><p>Hello</p></body></html>"
    result = await extractor.extract(data, "text/html", config)
    
    assert result is None


def test_extract_html_method():
    """Test _extract_html method directly."""
    from src.email.text_extractor import TextExtractor

    extractor = TextExtractor()
    
    data = b"<html><head><title>Test</title></head><body><p>Content</p></body></html>"
    result = extractor._extract_html(data)
    
    assert "Test" in result
    assert "Content" in result


def test_decode_utf8():
    """Test UTF-8 decoding."""
    from src.email.text_extractor import TextExtractor

    extractor = TextExtractor()
    
    data = "Hello Wörld!".encode("utf-8")
    result = extractor._decode_utf8(data)
    
    assert result == "Hello Wörld!"


def test_decode_utf8_with_invalid_chars():
    """Test UTF-8 decoding with invalid characters."""
    from src.email.text_extractor import TextExtractor

    extractor = TextExtractor()
    
    data = b"Hello \xff\xfe World"
    result = extractor._decode_utf8(data)
    
    assert "Hello" in result
    assert "World" in result
