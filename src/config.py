"""Configuration settings for Email Server."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    # Security
    require_auth: bool = False
    allowed_senders: list = []

    # Logging
    log_level: str = "INFO"
    log_file: str = ""  # Empty = stdout only (stateless). Set to path for file logging.

    # MCP
    mcp_enabled: bool = True
    mcp_port: int = 8001

    class Config:
        env_prefix = "EMAILSERVER_"
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
