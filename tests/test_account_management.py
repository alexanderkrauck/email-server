"""Account configuration writes remain tenant-scoped and credential-safe."""

from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.handlers import email_handler
from src.handlers.email_handler import MailAccountCreate, MailAccountUpdate
from src.models.base import Base
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.security.account_connect_tokens import issue_password_setup_token


@pytest.fixture
def account_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_create_and_update_never_return_password(account_db, monkeypatch):
    async def connection_ok(account):
        return {"imap": True, "smtp": True}

    monkeypatch.setattr(email_handler, "verify_account_connection", connection_ok)
    user = User(google_sub="owner", email="owner@example.com")
    account_db.add(user)
    account_db.commit()

    created = await email_handler.create_account(
        MailAccountCreate(
            name="Work",
            account_name="owner@example.com",
            provider="imap",
            host="imap.example.com",
            smtp_host="smtp.example.com",
            username="owner@example.com",
            password="initial-secret",
        ),
        user,
        account_db,
    )

    account_id = created["id"]
    account = account_db.get(SMTPConfig, account_id)
    assert account.password == "initial-secret"
    assert "initial-secret" not in str(created)
    assert "password" not in created
    assert "credential_ciphertext" not in created

    updated = await email_handler.update_account(
        account_id,
        MailAccountUpdate(
            host="imap2.example.com",
            password="rotated-secret",
        ),
        user,
        account_db,
    )

    account_db.refresh(account)
    assert account.host == "imap2.example.com"
    assert account.password == "rotated-secret"
    assert account.sync_state == "pending"
    assert account.backfill_complete is False
    assert "rotated-secret" not in str(updated)
    assert "password" not in updated
    assert "credential_ciphertext" not in updated


@pytest.mark.asyncio
async def test_failed_update_is_retained_and_cross_tenant_update_is_hidden(
    account_db,
    monkeypatch,
):
    async def connection_failed(account):
        return {"imap": False, "smtp": False}

    owner = User(google_sub="owner", email="owner@example.com")
    other = User(google_sub="other", email="other@example.com")
    account_db.add_all([owner, other])
    account_db.flush()
    account = SMTPConfig(
        owner_user_id=owner.id,
        provider="imap",
        auth_type="password",
        name="Work",
        account_name="owner@example.com",
        host="imap.example.com",
        port=993,
        smtp_host="smtp.example.com",
        smtp_port=465,
        username="owner@example.com",
        imap_use_ssl=True,
        imap_use_tls=False,
        smtp_use_ssl=True,
        smtp_use_tls=False,
        enabled=True,
    )
    account.password = "working-secret"
    account_db.add(account)
    account_db.commit()
    account_id = account.id
    monkeypatch.setattr(email_handler, "verify_account_connection", connection_failed)

    failed = await email_handler.update_account(
        account_id,
        MailAccountUpdate(host="broken.example.com", password="bad-secret"),
        owner,
        account_db,
    )
    account_db.refresh(account)
    assert failed["configuration_saved"] is True
    assert failed["connection_test"] == {"imap": False, "smtp": False}
    assert account.host == "broken.example.com"
    assert account.password == "bad-secret"
    assert account.sync_state == "error"

    with pytest.raises(HTTPException) as hidden:
        await email_handler.update_account(
            account_id,
            MailAccountUpdate(name="Stolen", verify_connection=False),
            other,
            account_db,
        )
    assert hidden.value.status_code == 404


@pytest.mark.asyncio
async def test_account_can_be_staged_without_password(account_db, monkeypatch):
    async def connection_must_not_run(account):
        raise AssertionError("Connection test ran without a credential")

    monkeypatch.setattr(
        email_handler,
        "verify_account_connection",
        connection_must_not_run,
    )
    user = User(google_sub="staged-owner", email="staged@example.com")
    account_db.add(user)
    account_db.commit()

    created = await email_handler.create_account(
        MailAccountCreate(
            name="Staged",
            account_name="staged@example.com",
            provider="imap",
            host="imap.example.com",
            smtp_host="smtp.example.com",
            username="staged@example.com",
        ),
        user,
        account_db,
    )

    account = account_db.get(SMTPConfig, created["id"])
    assert account.credential_ciphertext is None
    assert account.sync_state == "credentials_required"
    assert created["credential_configured"] is False
    assert created["configuration_saved"] is True
    assert "connection_test" not in created


class PasswordFormRequest:
    def __init__(self, session=None, body=b"", content_type="application/x-www-form-urlencoded"):
        self.session = session or {}
        self._body = body
        self.headers = {"content-type": content_type}

    async def body(self):
        return self._body


@pytest.mark.asyncio
async def test_password_only_form_stores_credential_and_consumes_link(account_db):
    owner = User(google_sub="form-owner", email="form@example.com")
    account_db.add(owner)
    account_db.flush()
    account = SMTPConfig(
        owner_user_id=owner.id,
        provider="imap",
        auth_type="password",
        name="Form Mail",
        account_name="form@example.com",
        host="wrong.example.com",
        port=993,
        smtp_host="wrong.example.com",
        smtp_port=465,
        username="form@example.com",
        credential_ciphertext=None,
        imap_use_ssl=True,
        imap_use_tls=False,
        smtp_use_ssl=True,
        smtp_use_tls=False,
        enabled=True,
        sync_state="credentials_required",
    )
    account_db.add(account)
    account_db.commit()
    token = issue_password_setup_token(owner.id, account.id, None)
    request = PasswordFormRequest()

    redirect = await email_handler.start_password_setup(
        account_id=account.id,
        token=token,
        request=request,
        db=account_db,
    )
    assert redirect.status_code == 303
    assert "token=" not in redirect.headers["location"]

    form = await email_handler.password_setup_form(
        account_id=account.id,
        request=request,
        db=account_db,
    )
    form_html = form.body.decode()
    assert form.status_code == 200
    assert form_html.count("<input ") == 1
    assert 'name="password"' in form_html
    assert 'name="host"' not in form_html
    assert token not in form_html

    secret = "form-only-secret"
    request._body = urlencode({"password": secret}).encode()
    saved = await email_handler.save_password_setup(
        account_id=account.id,
        request=request,
        db=account_db,
    )

    account_db.refresh(account)
    assert saved.status_code == 200
    assert secret not in saved.body.decode()
    assert secret not in account.credential_ciphertext
    assert account.password == secret
    assert account.host == "wrong.example.com"
    assert account.sync_state == "pending"
    assert "mail_password_setup" not in request.session
    assert email_handler._password_setup_account(
        account_db,
        account.id,
        token,
    ) is None


@pytest.mark.asyncio
async def test_password_setup_link_is_tenant_bound(account_db):
    owner = User(google_sub="link-owner", email="owner@example.com")
    other = User(google_sub="other-owner", email="other@example.com")
    account_db.add_all([owner, other])
    account_db.flush()
    account = SMTPConfig(
        owner_user_id=owner.id,
        provider="imap",
        auth_type="password",
        name="Private",
        account_name="owner@example.com",
        host="imap.example.com",
        port=993,
        smtp_host="smtp.example.com",
        smtp_port=465,
        username="owner@example.com",
        credential_ciphertext=None,
        imap_use_ssl=True,
        imap_use_tls=False,
        smtp_use_ssl=True,
        smtp_use_tls=False,
        enabled=True,
    )
    account_db.add(account)
    account_db.commit()
    wrong_owner_token = issue_password_setup_token(other.id, account.id, None)
    request = PasswordFormRequest()

    response = await email_handler.start_password_setup(
        account_id=account.id,
        token=wrong_owner_token,
        request=request,
        db=account_db,
    )

    assert response.status_code == 401
    assert "mail_password_setup" not in request.session


def test_dashboard_route_is_removed():
    from src.server import final_app

    assert all(getattr(route, "path", None) != "/dashboard" for route in final_app.routes)


@pytest.mark.asyncio
async def test_root_renders_gmail_confirmation_page():
    from src.server import root

    response = await root(connected="gmail")
    html = response.body.decode()

    assert response.status_code == 200
    assert "Gmail is ready" in html
    assert "application/json" not in response.headers["content-type"]


def test_gmail_connection_stores_generated_pkce_verifier(monkeypatch):
    class FakeFlow:
        code_verifier = None
        authorization_kwargs = None

        def authorization_url(self, **kwargs):
            self.authorization_kwargs = kwargs
            self.code_verifier = "generated-code-verifier"
            return "https://accounts.google.test/authorize", "state"

    flow = FakeFlow()
    monkeypatch.setattr(email_handler, "_gmail_flow", lambda state: flow)
    request = SimpleNamespace(session={})
    user = SimpleNamespace(id=42)

    response = email_handler._start_gmail_connection(request, user)

    assert response.headers["location"] == "https://accounts.google.test/authorize"
    assert request.session["gmail_connect_user_id"] == 42
    assert request.session["gmail_oauth_code_verifier"] == "generated-code-verifier"
    assert flow.authorization_kwargs == {
        "access_type": "offline",
        "prompt": "consent",
    }


def test_gmail_scope_change_recovery_requires_complete_scope_set():
    token = {"access_token": "access-token", "scope": "expanded"}
    warning = Warning("scope changed")
    warning.token = token
    warning.new_scope = [
        *email_handler.GMAIL_CONNECTION_SCOPES,
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    flow = SimpleNamespace(oauth2session=SimpleNamespace(token={}))

    assert email_handler._recover_gmail_scope_change(flow, warning) is True
    assert flow.oauth2session.token == token

    warning.new_scope = [email_handler.GMAIL_MAIL_SCOPE]
    flow.oauth2session.token = {}
    assert email_handler._recover_gmail_scope_change(flow, warning) is False
    assert flow.oauth2session.token == {}


@pytest.mark.asyncio
async def test_gmail_connection_restores_pkce_verifier_on_callback(
    account_db,
    monkeypatch,
):
    owner = User(google_sub="gmail-owner", email="owner@example.com")
    account_db.add(owner)
    account_db.commit()

    class FakeFlow:
        def __init__(self):
            self.code_verifier = None
            self.credentials = SimpleNamespace(
                token="access-token",
                refresh_token="refresh-token",
            )
            self.fetch_code = None

        def fetch_token(self, *, code):
            assert self.code_verifier == "stored-code-verifier"
            self.fetch_code = code

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "sub": "gmail-provider-subject",
                "email": "mailbox@gmail.com",
            }

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    flow = FakeFlow()
    monkeypatch.setattr(email_handler, "_gmail_flow", lambda state: flow)
    monkeypatch.setattr(
        email_handler.httpx,
        "AsyncClient",
        lambda **kwargs: FakeAsyncClient(),
    )
    request = SimpleNamespace(
        session={
            "gmail_oauth_state": "expected-state",
            "gmail_connect_user_id": owner.id,
            "gmail_oauth_code_verifier": "stored-code-verifier",
        }
    )

    response = await email_handler.gmail_callback(
        request=request,
        code="authorization-code",
        state="expected-state",
        db=account_db,
    )

    account = (
        account_db.query(SMTPConfig)
        .filter(SMTPConfig.owner_user_id == owner.id)
        .one()
    )
    assert flow.fetch_code == "authorization-code"
    assert account.provider == "gmail"
    assert account.auth_type == "oauth2"
    assert account.account_name == "mailbox@gmail.com"
    assert response.status_code == 307
