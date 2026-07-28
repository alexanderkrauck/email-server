"""Google-backed MCP identity and tenant principal resolution."""

import logging
import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastmcp.server.dependencies import get_access_token
from sqlalchemy.orm import Session

from src.config import settings
from src.database.connection import get_db
from src.models.user import User
from src.security.crypto import persistent_secret

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)
_mcp_auth_provider = None


@dataclass(frozen=True)
class IdentityClaims:
    subject: str
    email: str
    name: str | None = None


def build_mcp_auth_provider():
    """Build FastMCP's OAuth proxy when Google authentication is enabled."""
    global _mcp_auth_provider
    if settings.auth_mode != "google":
        return None
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError("Google auth requires EMAILSERVER_GOOGLE_CLIENT_ID and EMAILSERVER_GOOGLE_CLIENT_SECRET")

    from fastmcp.server.auth.providers.google import GoogleProvider

    _mcp_auth_provider = GoogleProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.public_base_url.rstrip("/"),
        resource_base_url=settings.public_base_url.rstrip("/"),
        required_scopes=settings.google_required_scopes,
        valid_scopes=settings.google_required_scopes,
        allowed_client_redirect_uris=settings.allowed_client_redirect_uris,
        jwt_signing_key=persistent_secret(settings.jwt_signing_key, "oauth-jwt.key"),
        require_authorization_consent=True,
        extra_authorize_params={"access_type": "offline", "prompt": "select_account"},
    )
    return _mcp_auth_provider


def _claims_from_mapping(claims: dict[str, Any], subject: str | None = None) -> IdentityClaims:
    sub = str(claims.get("sub") or subject or "")
    email = str(claims.get("email") or "")
    email_verified = claims.get("email_verified", True)
    if email_verified in {False, "false", "False", 0, "0"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google email is not verified")
    if not sub or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated identity lacks sub or email")
    return IdentityClaims(subject=sub, email=email.lower(), name=claims.get("name"))


def _identity_is_allowed(db: Session, claims: IdentityClaims) -> bool:
    if db.query(User).filter(User.google_sub == claims.subject).first():
        return True
    if settings.registration_mode == "open":
        return True
    return claims.subject in settings.allowed_google_subjects or claims.email in {
        email.lower() for email in settings.allowed_google_emails
    }


def resolve_user(db: Session, claims: IdentityClaims) -> User:
    """Resolve or provision a local user after applying registration policy."""
    user = db.query(User).filter(User.google_sub == claims.subject).first()
    if user:
        if user.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
        user.email = claims.email
        user.display_name = claims.name
        db.commit()
        db.refresh(user)
        return user

    if not _identity_is_allowed(db, claims):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not allowlisted")

    if settings.claim_legacy_accounts_on_first_login:
        legacy = db.query(User).filter(User.google_sub == settings.bootstrap_user_sub).first()
        if legacy:
            legacy.google_sub = claims.subject
            legacy.email = claims.email
            legacy.display_name = claims.name
            db.commit()
            db.refresh(legacy)
            logger.info("Claimed legacy mailbox ownership for Google subject %s", claims.subject)
            return legacy

    user = User(google_sub=claims.subject, email=claims.email, display_name=claims.name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def development_user(db: Session) -> User:
    user = db.query(User).filter(User.google_sub == settings.bootstrap_user_sub).first()
    if not user:
        user = User(google_sub=settings.bootstrap_user_sub, email=settings.bootstrap_user_email)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


async def current_mcp_user() -> User:
    """Resolve the current tool caller without accepting an owner ID from tool input."""
    from src.database.connection import SessionLocal

    with SessionLocal() as db:
        if settings.auth_mode == "development":
            return development_user(db)

        token = get_access_token()
        if token is None:
            raise PermissionError("Authentication required")
        claims = _claims_from_mapping(token.claims, token.subject)
        return resolve_user(db, claims)


async def verify_google_id_token(raw_token: str) -> IdentityClaims:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    payload = await asyncio.to_thread(
        id_token.verify_oauth2_token,
        raw_token,
        google_requests.Request(),
        settings.google_client_id,
    )
    return _claims_from_mapping(payload)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate account-management and HTTP mail APIs."""
    if settings.auth_mode == "development":
        return development_user(db)

    session_identity = request.session.get("identity") if hasattr(request, "session") else None
    if session_identity:
        return resolve_user(db, _claims_from_mapping(session_identity))

    if credentials:
        if _mcp_auth_provider:
            access = await _mcp_auth_provider.verify_token(credentials.credentials)
            if access:
                return resolve_user(db, _claims_from_mapping(access.claims, access.subject))
        try:
            claims = await verify_google_id_token(credentials.credentials)
            return resolve_user(db, claims)
        except Exception as exc:
            logger.info("Bearer authentication failed: %s", type(exc).__name__)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": 'Bearer resource_metadata="/.well-known/oauth-protected-resource/mcp"'},
    )


def owned_account_query(db: Session, user: User):
    from src.models.smtp_config import SMTPConfig

    return db.query(SMTPConfig).filter(SMTPConfig.owner_user_id == user.id)
