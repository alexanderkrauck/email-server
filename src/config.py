"""Configuration settings for Email Server."""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:////app/data/emailserver.db"
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

    # File Logging
    email_log_dir: str = "/app/data/emails"
    log_format: str = "json"  # json or text

    # Attachment Settings
    max_attachment_size: int = 10 * 1024 * 1024  # 10MB
    inline_attachment_size: int = 1024 * 1024  # 1MB - store in DB vs filesystem
    allowed_attachment_types: list = []  # Empty means all types allowed

    # Text-Only Storage (Global Settings - permissive defaults)
    # Global stronger negative: if global=False, account CANNOT enable it
    # Set all to False to allow accounts to configure as they wish
    store_text_only: bool = False  # Allow accounts to enable text-only storage
    max_attachment_size_text: int = 10 * 1024 * 1024  # Max size for text extraction

    # Text Extraction Settings (which types to extract text from)
    # Set to False to allow accounts to enable individually
    extract_pdf_text: bool = False  # Allow accounts to enable PDF -> text
    extract_docx_text: bool = False  # Allow accounts to enable DOCX -> text
    extract_image_text: bool = False  # OCR (off by default)
    extract_other_text: bool = False  # Allow accounts to enable other text extraction

    # Search Settings
    search_index_enabled: bool = False
    search_index_dir: str = "/app/data/search-index"

    # Security
    require_auth: bool = False
    allowed_senders: list = []  # Empty means all senders allowed

    # Logging
    log_level: str = "INFO"
    log_file: str = "/app/data/emailserver.log"

    # MCP
    mcp_enabled: bool = True
    mcp_port: int = 8001

    class Config:
        env_prefix = "EMAILSERVER_"
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()