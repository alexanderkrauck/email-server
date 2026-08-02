"""Small Gmail REST client for resumable mailbox synchronization."""

import asyncio
import base64
import json
from datetime import datetime, timezone

import httpx

from src.email.gmail_labels import location_for
from src.email.message_dates import parse_header_date
from src.email.smtp_client import SMTPClient
from src.security.provider_tokens import refresh_access_token


class GmailHistoryExpired(RuntimeError):
    """The saved Gmail history ID is no longer available."""


class GmailApiClient:
    BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        config,
        *,
        http_client: httpx.AsyncClient | None = None,
        access_token: str | None = None,
    ):
        self.config = config
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_client = http_client is None
        self._access_token = access_token
        self._parser = SMTPClient(config)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _token(self) -> str:
        if not self._access_token:
            self._access_token = await asyncio.to_thread(
                refresh_access_token, self.config.credential_ciphertext
            )
        return self._access_token

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        allow_missing: bool = False,
        history_request: bool = False,
    ) -> dict | None:
        for attempt in range(3):
            response = await self._client.get(
                f"{self.BASE_URL}/{path.lstrip('/')}",
                params=params,
                headers={"Authorization": f"Bearer {await self._token()}"},
            )
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                continue
            if response.status_code == 404:
                if history_request:
                    raise GmailHistoryExpired("Gmail history ID expired or is invalid")
                if allow_missing:
                    return None
            if response.status_code not in self.RETRYABLE_STATUSES or attempt == 2:
                response.raise_for_status()
                return response.json()
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 10.0) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            await asyncio.sleep(delay)
        raise RuntimeError("Gmail request retry loop exited unexpectedly")

    async def get_profile(self) -> dict:
        return await self._request("profile") or {}

    async def list_messages(self, *, page_token: str | None = None, max_results: int = 100) -> dict:
        params: dict[str, str | int] = {
            "maxResults": max_results,
            "includeSpamTrash": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._request("messages", params=params) or {}

    async def list_history(
        self, start_history_id: str, *, page_token: str | None = None
    ) -> dict:
        params = {"startHistoryId": start_history_id, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        return (
            await self._request("history", params=params, history_request=True)
            or {}
        )

    async def _post(self, path: str, payload: dict | None = None) -> dict | None:
        """POST with the same auth refresh and backoff the read path uses."""
        for attempt in range(3):
            response = await self._client.post(
                f"{self.BASE_URL}/{path.lstrip('/')}",
                json=payload if payload is not None else {},
                headers={"Authorization": f"Bearer {await self._token()}"},
            )
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                continue
            if response.status_code not in self.RETRYABLE_STATUSES or attempt == 2:
                response.raise_for_status()
                # trash and delete answer 204 with no body.
                return response.json() if response.content else None
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 10.0) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            await asyncio.sleep(delay)
        raise RuntimeError("Gmail request retry loop exited unexpectedly")

    async def modify_labels(
        self, message_id: str, *, add: list[str], remove: list[str]
    ) -> list[str]:
        """Apply label changes and return the labels the message now carries.

        messages.modify rather than batchModify: modify answers with the full
        updated resource, so the new state is server-confirmed in one round trip.
        batchModify answers 204, needing a second read to learn what happened,
        and costs more quota for the pair than modify does per message.
        """
        payload = {}
        if add:
            payload["addLabelIds"] = add
        if remove:
            payload["removeLabelIds"] = remove
        if not payload:
            return []
        result = await self._post(f"messages/{message_id}/modify", payload) or {}
        return sorted(str(label) for label in result.get("labelIds", []))

    async def trash_message(self, message_id: str) -> list[str]:
        """Move a message to Gmail's Bin."""
        result = await self._post(f"messages/{message_id}/trash") or {}
        return sorted(str(label) for label in result.get("labelIds", []))

    async def delete_message_permanently(self, message_id: str) -> None:
        """Remove a message outright. There is no undo for this on Gmail's side."""
        response = await self._client.post(
            f"{self.BASE_URL}/messages/{message_id}/delete",
            headers={"Authorization": f"Bearer {await self._token()}"},
        )
        if response.status_code == 405:
            response = await self._client.delete(
                f"{self.BASE_URL}/messages/{message_id}",
                headers={"Authorization": f"Bearer {await self._token()}"},
            )
        response.raise_for_status()

    async def create_draft(self, raw: bytes, *, thread_id: str | None = None) -> dict | None:
        """Store a draft, from the same RFC 5322 bytes the IMAP path appends."""
        message: dict = {"raw": base64.urlsafe_b64encode(raw).decode("ascii")}
        if thread_id:
            message["threadId"] = thread_id
        return await self._post("drafts", {"message": message})

    async def list_labels(self) -> list[dict]:
        result = await self._request("labels") or {}
        return list(result.get("labels", []))

    async def get_message(self, message_id: str, *, raw: bool = True) -> dict | None:
        params = {"format": "raw" if raw else "metadata"}
        return await self._request(
            f"messages/{message_id}",
            params=params,
            allow_missing=True,
        )

    async def get_parsed_message(self, message_id: str) -> dict | None:
        provider_message = await self.get_message(message_id)
        if not provider_message:
            return None
        encoded = provider_message.get("raw")
        if not encoded:
            raise RuntimeError(f"Gmail message {message_id} did not include an RFC822 payload")
        raw_email = self.decode_raw(encoded)
        labels = sorted(provider_message.get("labelIds", []))
        parsed = await self._parser._parse_email(
            raw_email,
            message_id,
            # Gmail has no folders, so a location is projected from the labels.
            # "gmail" told search and every write nothing at all.
            folder=location_for(labels),
            uid_validity=None,
            flags=json.dumps(labels),
        )
        if not parsed:
            return None
        parsed["provider_message_id"] = str(provider_message["id"])
        parsed["provider_thread_id"] = str(provider_message.get("threadId") or "") or None
        parsed["imap_uid"] = None
        parsed["uid_validity"] = None
        if parsed.get("email_date") is None and provider_message.get("internalDate"):
            parsed["email_date"] = datetime.fromtimestamp(
                int(provider_message["internalDate"]) / 1000, tz=timezone.utc
            )
        return parsed

    async def get_raw_message(self, message_id: str) -> bytes | None:
        provider_message = await self.get_message(message_id)
        if not provider_message:
            return None
        encoded = provider_message.get("raw")
        return self.decode_raw(encoded) if encoded else None

    @staticmethod
    def decode_raw(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @staticmethod
    def message_date(provider_message: dict) -> datetime | None:
        """Best-effort date helper for metadata-only callers."""
        internal_date = provider_message.get("internalDate")
        if internal_date:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        for header in provider_message.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "date":
                return parse_header_date(header.get("value", ""))
        return None
