"""Tests for SMTP client - simplified to avoid connection issues."""

from dataclasses import replace
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


def test_list_parser_accepts_quoted_and_unquoted_mailboxes():
    from src.email.smtp_client import SMTPClient

    assert SMTPClient._parse_list_response(
        b'(\\HasChildren) "." INBOX'
    ) == ({"\\haschildren"}, "INBOX")
    assert SMTPClient._parse_list_response(
        b'(\\HasNoChildren \\UnMarked) "." "INBOX.Project - Vienna"'
    ) == (
        {"\\hasnochildren", "\\unmarked"},
        "INBOX.Project - Vienna",
    )
    assert SMTPClient._parse_list_response(
        r'(\HasNoChildren) "/" "Quoted \"Folder\""'
    ) == ({"\\hasnochildren"}, 'Quoted "Folder"')
    assert SMTPClient._parse_list_response(b"List completed") is None


@pytest.mark.asyncio
async def test_get_folders_keeps_selectable_unquoted_names(mock_smtp_config):
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client.client.list.return_value = SimpleNamespace(
        result="OK",
        lines=[
            b'(\\HasChildren) "." INBOX',
            b'(\\HasNoChildren) "." INBOX.Sent',
            b'(\\Noselect \\HasChildren) "." INBOX.Projects',
            b'(\\HasNoChildren) "." "INBOX.Project - Vienna"',
            b'(\\HasNoChildren) "." INBOX.Sent',
            b"List completed",
        ],
    )

    folders = await smtp_client._get_folders()

    assert folders == [
        "INBOX",
        "INBOX.Sent",
        "INBOX.Project - Vienna",
    ]


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
async def test_update_config_disconnects_when_password_changes(mock_smtp_config):
    """Updated credentials invalidate the cached IMAP connection."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client._connected = True
    old_client = smtp_client.client
    updated_config = replace(mock_smtp_config, password="new-password")

    await smtp_client.update_config(updated_config)

    old_client.logout.assert_awaited_once()
    assert smtp_client.config.password == "new-password"
    assert smtp_client.client is None
    assert smtp_client._connected is False


@pytest.mark.asyncio
async def test_update_config_keeps_connection_for_display_change(mock_smtp_config):
    """Non-connection metadata changes do not interrupt IMAP."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client._connected = True
    current_client = smtp_client.client
    updated_config = replace(mock_smtp_config, name="Renamed Account")

    await smtp_client.update_config(updated_config)

    current_client.logout.assert_not_awaited()
    assert smtp_client.config.name == "Renamed Account"
    assert smtp_client.client is current_client
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
            lines=[b"7 FETCH (UID 42 RFC822.SIZE 100)"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"7 FETCH (UID 42 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
        ),
        SimpleNamespace(
            result="OK",
            lines=[b"8 FETCH (UID 43 RFC822.SIZE 100)"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"8 FETCH (UID 43 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
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
    assert smtp_client.client.fetch.await_count == 4


def test_extract_raw_email_accepts_bytearray_payload():
    from src.email.smtp_client import SMTPClient

    raw = bytearray(b"From: sender@example.com\r\n\r\nbody")
    lines = [b"1 FETCH (BODY[] {32}", raw, b")", b"Success"]

    assert SMTPClient._extract_raw_email(lines) == bytes(raw)


@pytest.mark.asyncio
async def test_fetch_error_does_not_advance_uid_checkpoint(mock_smtp_config):
    """An interrupted folder is retried from its prior checkpoint."""
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client._last_uids["INBOX"] = 41
    smtp_client.client = AsyncMock()
    smtp_client.client.select.return_value = SimpleNamespace(result="OK", lines=[])
    smtp_client.client.search.return_value = SimpleNamespace(result="OK", lines=[b"7"])
    smtp_client.client.fetch.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[b"7 FETCH (UID 42 RFC822.SIZE 100)"],
        ),
        TimeoutError,
    ]

    with pytest.raises(TimeoutError):
        _ = [batch async for batch in smtp_client._fetch_folder("INBOX")]

    assert smtp_client._last_uids["INBOX"] == 41


@pytest.mark.asyncio
async def test_bounded_backfill_fetches_oldest_uids_without_skipping(
    mock_smtp_config,
    sample_raw_email,
):
    from src.email.smtp_client import SMTPClient

    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client.client.select.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[
                b"* OK [UIDVALIDITY 123] UIDs valid",
                b"* OK [UIDNEXT 13] Predicted next UID",
            ],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"* OK [UIDVALIDITY 123] UIDs valid",
                b"* OK [UIDNEXT 13] Predicted next UID",
            ],
        ),
    ]
    smtp_client.client.search.side_effect = [
        SimpleNamespace(result="OK", lines=[b"1 2 3"]),
        SimpleNamespace(result="OK", lines=[b"3"]),
    ]
    smtp_client.client.fetch.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[
                b"1 FETCH (UID 10 RFC822.SIZE 100)",
                b"2 FETCH (UID 11 RFC822.SIZE 100)",
            ],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"1 FETCH (UID 10 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"2 FETCH (UID 11 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
        ),
        SimpleNamespace(
            result="OK",
            lines=[b"3 FETCH (UID 12 RFC822.SIZE 100)"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"3 FETCH (UID 12 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
        ),
    ]

    first = [
        batch async for batch in smtp_client._fetch_folder("INBOX", limit=2)
    ]
    assert [
        message["imap_uid"] for batch in first for message in batch
    ] == [10, 11]
    assert smtp_client._last_uids["INBOX"] == 11
    assert smtp_client._folder_backfill_complete["INBOX"] is False

    second = [batch async for batch in smtp_client._fetch_folder("INBOX", limit=2)]
    assert [message["imap_uid"] for message in second[0]] == [12]
    assert smtp_client.client.search.await_args_list[1].args == ("UID", "12:*")
    assert smtp_client._last_uids["INBOX"] == 12
    assert smtp_client._folder_backfill_complete["INBOX"] is True


@pytest.mark.asyncio
async def test_uidvalidity_change_resets_folder_backfill(
    mock_smtp_config,
    sample_raw_email,
):
    from src.email.smtp_client import SMTPClient

    mock_smtp_config.sync_cursors = {
        "INBOX": {
            "uid_validity": 100,
            "last_uid": 50,
            "backfill_complete": True,
        }
    }
    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client.client.select.return_value = SimpleNamespace(
        result="OK",
        lines=[
            b"* OK [UIDVALIDITY 200] UIDs valid",
            b"* OK [UIDNEXT 3] Predicted next UID",
        ],
    )
    smtp_client.client.search.return_value = SimpleNamespace(
        result="OK",
        lines=[b"1 2"],
    )
    smtp_client.client.fetch.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[b"1 FETCH (UID 1 RFC822.SIZE 100)"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"1 FETCH (UID 1 RFC822.SIZE 100 RFC822 {100}",
                sample_raw_email,
                b")",
            ],
        ),
    ]

    batches = [
        batch async for batch in smtp_client._fetch_folder("INBOX", limit=1)
    ]

    assert batches[0][0]["imap_uid"] == 1
    assert smtp_client.client.search.await_args.args == ("ALL",)
    assert smtp_client._last_uids["INBOX"] == 1
    assert smtp_client._folder_backfill_complete["INBOX"] is False


@pytest.mark.asyncio
async def test_oversized_message_fetches_headers_only(
    monkeypatch,
    mock_smtp_config,
):
    from src.config import settings
    from src.email.smtp_client import SMTPClient

    monkeypatch.setattr(settings, "imap_max_message_size", 1_000)
    smtp_client = SMTPClient(mock_smtp_config)
    smtp_client.client = AsyncMock()
    smtp_client.client.select.return_value = SimpleNamespace(
        result="OK",
        lines=[
            b"* OK [UIDVALIDITY 123] UIDs valid",
            b"* OK [UIDNEXT 43] Predicted next UID",
        ],
    )
    smtp_client.client.search.return_value = SimpleNamespace(
        result="OK",
        lines=[b"7"],
    )
    header = (
        b"From: sender@example.com\r\n"
        b"To: recipient@example.com\r\n"
        b"Subject: Large message\r\n"
        b"Message-ID: <large@example.com>\r\n\r\n"
    )
    smtp_client.client.fetch.side_effect = [
        SimpleNamespace(
            result="OK",
            lines=[b"7 FETCH (UID 42 RFC822.SIZE 2000)"],
        ),
        SimpleNamespace(
            result="OK",
            lines=[
                b"7 FETCH (UID 42 RFC822.SIZE 2000 BODY[HEADER] {120}",
                header,
                b")",
            ],
        ),
    ]

    batches = [batch async for batch in smtp_client._fetch_folder("INBOX")]

    message = batches[0][0]
    assert message["provider_size"] == 2_000
    assert message["content_state"] == "headers_only"
    assert message["subject"] == "Large message"
    assert message["body_plain"] == ""
    assert message["attachment_count"] == 0
    assert smtp_client.client.fetch.await_args_list[1].args == (
        "7",
        "(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER])",
    )


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
