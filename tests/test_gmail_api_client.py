"""Gmail API synchronization client tests."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.email.email_processor import EmailProcessor
from src.email.gmail_api_client import GmailApiClient, GmailHistoryExpired


def _config():
    return SimpleNamespace(
        id=7,
        name="OAuth Gmail",
        credential_ciphertext="encrypted-refresh-token",
        sync_cursors={},
    )


@pytest.mark.asyncio
async def test_get_parsed_message_uses_gmail_provider_ids(sample_raw_email):
    encoded = base64.urlsafe_b64encode(sample_raw_email).decode().rstrip("=")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-access-token"
        assert request.url.params["format"] == "raw"
        return httpx.Response(
            200,
            json={
                "id": "18fabc123",
                "threadId": "thread-9",
                "labelIds": ["STARRED", "INBOX"],
                "internalDate": "1736937000000",
                "raw": encoded,
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GmailApiClient(
        _config(), http_client=http_client, access_token="test-access-token"
    )
    try:
        message = await client.get_parsed_message("18fabc123")
    finally:
        await http_client.aclose()

    assert message["provider_message_id"] == "18fabc123"
    assert message["provider_thread_id"] == "thread-9"
    # Gmail has no folders; a location is projected from the labels.
    assert message["folder"] == "INBOX"
    assert message["imap_uid"] is None
    assert json.loads(message["flags"]) == ["INBOX", "STARRED"]
    assert message["raw_email"] == sample_raw_email


@pytest.mark.asyncio
async def test_history_404_has_explicit_expired_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Not Found"}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GmailApiClient(
        _config(), http_client=http_client, access_token="test-access-token"
    )
    try:
        with pytest.raises(GmailHistoryExpired):
            await client.list_history("123")
    finally:
        await http_client.aclose()


def test_history_change_classification():
    changed, deleted = EmailProcessor._gmail_history_changes(
        {
            "history": [
                {
                    "messages": [{"id": "metadata-only"}],
                    "messagesAdded": [{"message": {"id": "added"}}],
                    "labelsRemoved": [{"message": {"id": "labels"}}],
                    "messagesDeleted": [{"message": {"id": "deleted"}}],
                }
            ]
        }
    )

    assert changed == {"metadata-only", "added", "labels"}
    assert deleted == {"deleted"}


def test_gmail_raw_decoder_accepts_unpadded_base64url():
    payload = b"\xffbinary email\r\n"
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert GmailApiClient.decode_raw(encoded) == payload


@pytest.mark.asyncio
async def test_processor_commits_gmail_messages_in_bounded_chunks(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "gmail_request_concurrency", 2)
    client = SimpleNamespace()

    async def get_message(message_id):
        if message_id == "gone":
            return None
        return {"provider_message_id": message_id}

    client.get_parsed_message = get_message
    processor = EmailProcessor()
    processor._process_emails = AsyncMock()

    processed, missing = await processor._fetch_and_process_gmail_messages(
        client,
        ["one", "two", "gone", "three", "four"],
        sync_generation=8,
    )

    assert processed == 4
    assert missing == {"gone"}
    assert processor._process_emails.await_count == 3
    batches = [
        call.args[0]
        for call in processor._process_emails.await_args_list
    ]
    assert [len(batch) for batch in batches] == [2, 1, 1]
    assert all(
        message["last_seen_sync_generation"] == 8
        for batch in batches
        for message in batch
    )
