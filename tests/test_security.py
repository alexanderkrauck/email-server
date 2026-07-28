"""Security boundary tests."""

import pytest


def test_credentials_are_encrypted_and_round_trip():
    from src.security.crypto import decrypt_secret, encrypt_secret

    encrypted = encrypt_secret("not-stored-in-plaintext")

    assert encrypted.startswith("enc:v1:")
    assert "not-stored-in-plaintext" not in encrypted
    assert decrypt_secret(encrypted) == "not-stored-in-plaintext"


def test_download_token_is_bound_to_attachment_and_expiry_claim():
    from src.security.download_tokens import issue_download_token, verify_download_token

    token = issue_download_token(user_id=7, attachment_id=42)

    assert verify_download_token(token, attachment_id=42) == 7
    with pytest.raises(ValueError):
        verify_download_token(token, attachment_id=43)
    with pytest.raises(ValueError):
        verify_download_token(token[:-1] + ("A" if token[-1] != "A" else "B"), attachment_id=42)


@pytest.mark.asyncio
async def test_google_oauth_provider_emits_audience_bound_mcp_challenge():
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.google import GoogleProvider
    from httpx import ASGITransport, AsyncClient

    provider = GoogleProvider(
        client_id="test.apps.googleusercontent.com",
        client_secret="test-secret",
        base_url="https://mail.example.com",
        resource_base_url="https://mail.example.com",
        allowed_client_redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        jwt_signing_key="long-enough-test-signing-key",
    )
    app = FastMCP("auth-test", auth=provider).http_app(path="/mcp")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://mail.example.com") as client:
        response = await client.post(
            "/mcp",
            headers={"accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == (
            'Bearer resource_metadata="https://mail.example.com/.well-known/oauth-protected-resource/mcp"'
        )
        assert (await client.get("/.well-known/oauth-protected-resource/mcp")).status_code == 200
        assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 200


def test_unverified_google_email_is_rejected():
    from fastapi import HTTPException

    from src.security.auth import _claims_from_mapping

    with pytest.raises(HTTPException) as error:
        _claims_from_mapping(
            {"sub": "subject", "email": "unverified@example.com", "email_verified": False}
        )

    assert error.value.status_code == 401
