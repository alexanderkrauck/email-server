"""Outbound SMTP result handling."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_partial_recipient_refusal_is_not_reported_as_success():
    from src.email.smtp_sender import EmailSender

    config = SimpleNamespace(
        id=1,
        name="Test",
        account_name="sender@example.com",
        username="sender@example.com",
    )
    sender = EmailSender(config)
    sender._server = MagicMock()
    sender._server.send_message.return_value = {"refused@example.com": (550, b"rejected")}

    result = await sender.send_email(
        to_addresses=["accepted@example.com", "refused@example.com"],
        subject="Test",
        body_text="Body",
    )

    assert result["success"] is False
    assert result["delivery_state"] == "partial"
    assert result["refused_recipients"] == ["refused@example.com"]


@pytest.mark.asyncio
async def test_plaintext_smtp_transport_is_rejected():
    from src.email.smtp_sender import EmailSender

    config = SimpleNamespace(
        id=1,
        name="Test",
        host="smtp.example.com",
        port=25,
        smtp_host="smtp.example.com",
        smtp_port=25,
        username="sender@example.com",
        password="secret",
        auth_type="password",
        smtp_use_ssl=False,
        smtp_use_tls=False,
    )

    assert await EmailSender(config).connect() is False
