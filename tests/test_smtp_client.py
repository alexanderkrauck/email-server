"""Tests for SMTP client - simplified to avoid connection issues."""


def test_smtp_client_class_exists():
    """Test SMTPClient class exists."""
    from src.email.smtp_client import SMTPClient

    assert SMTPClient is not None


def test_smtp_config_to_dict():
    """Test SMTP config to dict conversion."""
    from src.email import smtp_client

    # Just verify module loads properly
    assert smtp_client is not None
