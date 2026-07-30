"""Static-token authentication for deployments without a Google project."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings
from src.models.base import Base
from src.models.user import User
from src.security.auth import (
    MIN_API_TOKEN_LENGTH,
    build_mcp_auth_provider,
    current_mcp_user,
    get_current_user,
    local_owner_user,
)

TOKEN = "z" * MIN_API_TOKEN_LENGTH


@pytest.fixture
def owner_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def single_user_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "single_user")
    monkeypatch.setattr(settings, "api_token", TOKEN)
    return settings


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _Request:
    session: dict = {}


@pytest.mark.asyncio
async def test_configured_token_is_accepted_and_every_other_token_is_rejected(
    single_user_mode,
):
    verifier = build_mcp_auth_provider()

    accepted = await verifier.verify_token(TOKEN)
    assert accepted is not None
    assert accepted.claims["sub"] == settings.bootstrap_user_sub

    assert await verifier.verify_token(TOKEN[:-1] + "y") is None
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token(TOKEN.upper()) is None


def test_short_or_missing_token_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "single_user")

    for weak in ("", "short", "z" * (MIN_API_TOKEN_LENGTH - 1)):
        monkeypatch.setattr(settings, "api_token", weak)
        with pytest.raises(RuntimeError, match="EMAILSERVER_API_TOKEN"):
            build_mcp_auth_provider()


def test_single_user_mode_needs_no_google_credentials(monkeypatch, single_user_mode):
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")

    assert build_mcp_auth_provider() is not None


@pytest.mark.asyncio
async def test_http_api_requires_the_configured_bearer_token(single_user_mode, owner_db):
    authorized = await get_current_user(_Request(), _credentials(TOKEN), owner_db)
    assert authorized.google_sub == settings.bootstrap_user_sub

    for rejected in (None, _credentials(""), _credentials(TOKEN[:-1] + "y")):
        with pytest.raises(HTTPException) as error:
            await get_current_user(_Request(), rejected, owner_db)
        assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_http_api_ignores_browser_sessions_in_single_user_mode(
    single_user_mode, owner_db
):
    request = _Request()
    request.session = {"identity": {"sub": "someone-else", "email": "other@example.com"}}

    with pytest.raises(HTTPException) as error:
        await get_current_user(request, None, owner_db)

    assert error.value.status_code == 401
    assert owner_db.query(User).filter(User.google_sub == "someone-else").count() == 0


@pytest.mark.asyncio
async def test_mcp_caller_resolves_to_the_single_owner(single_user_mode, owner_db):
    session_factory = sessionmaker(bind=owner_db.get_bind())

    with (
        patch("src.database.connection.SessionLocal", session_factory),
        patch("src.security.auth.get_access_token", return_value=object()),
    ):
        user = await current_mcp_user()

    assert user.google_sub == settings.bootstrap_user_sub


@pytest.mark.asyncio
async def test_mcp_caller_without_a_verified_token_is_refused(single_user_mode, owner_db):
    session_factory = sessionmaker(bind=owner_db.get_bind())

    with (
        patch("src.database.connection.SessionLocal", session_factory),
        patch("src.security.auth.get_access_token", return_value=None),
        pytest.raises(PermissionError),
    ):
        await current_mcp_user()


def test_single_owner_is_created_once_and_reused(owner_db):
    first = local_owner_user(owner_db)
    second = local_owner_user(owner_db)

    assert first.id == second.id
    assert owner_db.query(User).count() == 1
