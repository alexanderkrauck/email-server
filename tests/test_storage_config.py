"""Tests for storage config resolver."""

from unittest.mock import patch


def test_resolve_storage_config_defaults():
    """Test default storage config resolution."""
    from src.storage_config.resolver import resolve_storage_config

    with patch("src.storage_config.resolver.settings") as mock_settings:
        mock_settings.store_text_only = False
        mock_settings.max_attachment_size_text = 1000000
        mock_settings.extract_pdf_text = True
        mock_settings.extract_docx_text = True
        mock_settings.extract_image_text = True
        mock_settings.extract_other_text = True

        config = resolve_storage_config(None)

        assert config.store_text_only is False
        assert config.max_attachment_size == 1000000
        assert config.extract_pdf_text is True
        assert config.extract_docx_text is True


def test_resolve_storage_config_with_none_override():
    """Test storage config with None overrides (should use global)."""
    from src.storage_config.resolver import resolve_storage_config

    class MockSMTPConfig:
        store_text_only_override = None
        max_attachment_size_override = None
        extract_pdf_text_override = None
        extract_docx_text_override = None
        extract_image_text_override = None
        extract_other_text_override = None

    with patch("src.storage_config.resolver.settings") as mock_settings:
        mock_settings.store_text_only = True
        mock_settings.max_attachment_size_text = 500000
        mock_settings.extract_pdf_text = True
        mock_settings.extract_docx_text = False
        mock_settings.extract_image_text = True
        mock_settings.extract_other_text = False

        config = resolve_storage_config(MockSMTPConfig())

        assert config.store_text_only is True
        assert config.max_attachment_size == 500000
        assert config.extract_pdf_text is True


def test_should_extract_text():
    """Test should_extract_text function."""
    from src.storage_config.resolver import StorageConfig, should_extract_text

    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=True,
        extract_docx_text=True,
        extract_image_text=True,
        extract_other_text=True,
    )

    assert should_extract_text(config, "application/pdf") is True
    assert should_extract_text(config, "application/msword") is True
    assert should_extract_text(config, "image/png") is True
    assert should_extract_text(config, "text/plain") is True


def test_should_extract_text_disabled():
    """Test should_extract_text when extraction is disabled."""
    from src.storage_config.resolver import StorageConfig, should_extract_text

    config = StorageConfig(
        store_text_only=False,
        max_attachment_size=1000000,
        extract_pdf_text=False,
        extract_docx_text=False,
        extract_image_text=False,
        extract_other_text=False,
    )

    assert should_extract_text(config, "application/pdf") is False
    assert should_extract_text(config, "application/msword") is False
    assert should_extract_text(config, "image/png") is False


def test_resolve_boolean_global_false():
    """Test boolean resolution when global is False."""
    from src.storage_config.resolver import _resolve_boolean

    # Global False should always return False
    assert _resolve_boolean(False, None) is False
    assert _resolve_boolean(False, True) is False
    assert _resolve_boolean(False, False) is False


def test_resolve_boolean_global_true():
    """Test boolean resolution when global is True."""
    from src.storage_config.resolver import _resolve_boolean

    # Global True with no override should return True
    assert _resolve_boolean(True, None) is True
    # Global True with account True should return True
    assert _resolve_boolean(True, True) is True
    # Global True with account False should return False
    assert _resolve_boolean(True, False) is False


def test_resolve_max_value():
    """Test max value resolution."""
    from src.storage_config.resolver import _resolve_max_value

    # No override
    assert _resolve_max_value(1000, None) == 1000
    # With override (smaller wins)
    assert _resolve_max_value(1000, 500) == 500
    # With override (larger loses)
    assert _resolve_max_value(500, 1000) == 500
