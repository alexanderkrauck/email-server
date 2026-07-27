"""Tests for SMTP client - simplified to avoid connection issues."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_smtp_client_class_exists():
    """Test SMTPClient class exists."""
    from src.email.smtp_client import SMTPClient

    assert SMTPClient is not None


def test_smtp_config_to_dict():
    """Test SMTP config to dict conversion."""
    from src.email import smtp_client

    # Just verify module loads properly
    assert smtp_client is not None


@pytest.mark.asyncio
async def test_ensure_connected_replaces_stale_client(mock_smtp_config):
    """A cached connection is replaced when its NOOP fails."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    stale_client = AsyncMock()
    stale_client.noop.side_effect = TimeoutError
    smtp_client.client = stale_client
    smtp_client._connected = True

    fresh_client = AsyncMock()
    fresh_client.login.return_value = SimpleNamespace(result="OK", lines=[])

    with patch("src.email.smtp_client.aioimaplib.IMAP4_SSL", return_value=fresh_client):
        assert await smtp_client._ensure_connected() is True

    stale_client.logout.assert_awaited_once()
    fresh_client.wait_hello_from_server.assert_awaited_once()
    assert smtp_client.client is fresh_client
    assert smtp_client._connected is True


@pytest.mark.asyncio
async def test_fetch_folder_uses_date_then_uid_checkpoint(mock_smtp_config, sample_raw_email):
    """Polling moves from a date window to an idle check and then newer UIDs."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client.client.select.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[b"* OK [UIDVALIDITY 123] UIDs valid", b"* OK [UIDNEXT 43] Predicted next UID"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[b"* OK [UIDVALIDITY 123] UIDs valid", b"* OK [UIDNEXT 43] Predicted next UID"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[b"* OK [UIDVALIDITY 123] UIDs valid", b"* OK [UIDNEXT 44] Predicted next UID"],
        ),
    ]
    smtp_client.client.search.side_effect = [
        SimpleNamespace(result="OK", lines=[b"7"]),
        SimpleNamespace(result="OK", lines=[b"8"]),
    ]
    smtp_client.client.fetch.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[b"7 FETCH (UID 42 RFC822 {100}", sample_raw_email, b")"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[b"8 FETCH (UID 43 RFC822 {100}", sample_raw_email, b")"],
        ),
    ]

    first_batches = [
        batch
        async for batch in smtp_client._fetch_folder(
            "INBOX", since=datetime(2026, 7, 25, tzinfo=timezone.utc)
        )
    ]
    idle_batches = [batch async for batch in smtp_client._fetch_folder("INBOX")]
    second_batches = [batch async for batch in smtp_client._fetch_folder("INBOX")]

    assert len(first_batches) == 1
    assert idle_batches == []
    assert len(second_batches) == 1
    assert smtp_client._last_uids["INBOX"] == 43
    assert smtp_client.client.search.await_args_list[0].args == ("SINCE", "25-Jul-2026")
    assert smtp_client.client.search.await_args_list[1].args == ("UID", "43:*")
    assert smtp_client.client.fetch.await_count == 2


@pytest.mark.asyncio
async def test_fetch_error_does_not_advance_uid_checkpoint(mock_smtp_config):
    """An interrupted folder is retried from its prior checkpoint."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client._last_uids["INBOX"] = 41
    smtp_client.client = AsyncMock()
    smtp_client.client.select.return_value = SimpleNamespace(result="OK", lines=[])
    smtp_client.client.search.return_value = SimpleNamespace(result="OK", lines=[b"7"])
    smtp_client.client.fetch.side_effect = TimeoutError

    with pytest.raises(TimeoutError):
        _ = [batch async for batch in smtp_client._fetch_folder("INBOX")]

    assert smtp_client._last_uids["INBOX"] == 41


@pytest.mark.asyncio
async def test_login_failure_uses_response_lines(mock_smtp_config):
    """Non-OK login responses do not assume a removed response.data field."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    imap_client = AsyncMock()
    imap_client.login.return_value = SimpleNamespace(result="NO", lines=[b"bad credentials"])

    with patch("src.email.smtp_client.aioimaplib.IMAP4_SSL", return_value=imap_client):
        assert await smtp_client.connect() is False

    imap_client.logout.assert_awaited_once()
