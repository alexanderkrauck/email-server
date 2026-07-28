"""Configuration settings for Email Server."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EMAILSERVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    # Database
    database_url: str = "postgresql://emailserver:emailserver@postgres:5432/emailserver"
    database_echo: bool = False

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False

    # Email Server Settings
    smtp_host: str = "0.0.0.0"
    smtp_port: int = 2525

    # Email Processing
    email_check_interval: int = 30  # seconds
    max_emails_per_batch: int = 50
    deletion_reconcile_interval: int = 21_600
    upstream_delete_policy: Literal["hard_delete", "tombstone", "retain"] = "hard_delete"

    # Attachment Settings
    max_attachment_size: int = 10 * 1024 * 1024  # 10MB

    # Text-Only Storage (Global Settings - permissive defaults)
    # Global stronger negative: if global=False, account CANNOT enable it
    store_text_only: bool = False
    max_attachment_size_text: int = 10 * 1024 * 1024  # Max size for text extraction

    # Text Extraction Settings (which types to extract text from)
    extract_pdf_text: bool = True
    extract_docx_text: bool = True
    extract_image_text: bool = True
    extract_other_text: bool = True

    # Security and identity
    # Development mode is safe only while Docker binds the service to loopback.
    auth_mode: Literal["development", "google"] = "development"
    public_base_url: str = "http://localhost:8002"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_required_scopes: list[str] = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    allowed_client_redirect_uris: list[str] = [
        "https://claude.ai/api/mcp/auth_callback",
        "http://localhost:*",
        "http://127.0.0.1:*",
    ]
    registration_mode: Literal["open", "allowlist"] = "allowlist"
    allowed_google_subjects: list[str] = []
    allowed_google_emails: list[str] = []
    bootstrap_user_sub: str = "development-owner"
    bootstrap_user_email: str = "local-owner@example.invalid"
    claim_legacy_accounts_on_first_login: bool = False
    data_dir: str = "/data"
    credential_encryption_key: str = ""
    jwt_signing_key: str = ""
    session_secret: str = ""
    attachment_token_ttl_seconds: int = 300
    extraction_timeout_seconds: int = 30
    max_extracted_text_chars: int = 250_000
    max_message_body_chars: int = 250_000
    max_pdf_pages: int = 100
    regex_statement_timeout_ms: int = 2_000
    max_regex_pattern_length: int = 256
    max_send_recipients: int = 50
    max_sends_per_minute: int = 20
    max_accounts_per_user: int = 20
    max_outbound_attachment_bytes: int = 25 * 1024 * 1024

    # Logging
    log_level: str = "INFO"
    log_file: str = ""  # Empty = stdout only (stateless). Set to path for file logging.

    # MCP
    mcp_enabled: bool = True
    mcp_port: int = 8001

settings = Settings()


def data_path(filename: str) -> Path:
    """Return a path in the persistent private data directory."""
    return Path(settings.data_dir) / filename
