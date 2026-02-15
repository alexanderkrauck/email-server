"""Configuration resolver with global stronger negative logic."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.models.smtp_config import SMTPConfig

from src.config import settings
from src.models.smtp_config import SMTPConfig


@dataclass
class StorageConfig:
    """Resolved storage configuration for an account."""

    store_text_only: bool
    max_attachment_size: int
    extract_pdf_text: bool
    extract_docx_text: bool
    extract_image_text: bool
    extract_other_text: bool


def resolve_storage_config(smtp_config: Optional[SMTPConfig] = None) -> StorageConfig:
    """
    Resolve effective storage configuration for an account.

    Global stronger negative rule:
    - If global = False (disabled), account CANNOT enable it
    - If global = True (enabled), account CAN disable it

    This means: effective_value = min(global, account_override)
    For booleans: False wins over True
    """
    global_store_text_only = settings.store_text_only
    global_max_attachment_size = settings.max_attachment_size_text
    global_extract_pdf = settings.extract_pdf_text
    global_extract_docx = settings.extract_docx_text
    global_extract_image = settings.extract_image_text
    global_extract_other = settings.extract_other_text

    if smtp_config is None:
        return StorageConfig(
            store_text_only=global_store_text_only,
            max_attachment_size=global_max_attachment_size,
            extract_pdf_text=global_extract_pdf,
            extract_docx_text=global_extract_docx,
            extract_image_text=global_extract_image,
            extract_other_text=global_extract_other,
        )

    account_store_text_only = smtp_config.store_text_only_override
    account_max_size = smtp_config.max_attachment_size_override
    account_extract_pdf = smtp_config.extract_pdf_text_override
    account_extract_docx = smtp_config.extract_docx_text_override
    account_extract_image = smtp_config.extract_image_text_override
    account_extract_other = smtp_config.extract_other_text_override

    return StorageConfig(
        store_text_only=_resolve_boolean(global_store_text_only, account_store_text_only),
        max_attachment_size=_resolve_max_value(global_max_attachment_size, account_max_size),
        extract_pdf_text=_resolve_boolean(global_extract_pdf, account_extract_pdf),
        extract_docx_text=_resolve_boolean(global_extract_docx, account_extract_docx),
        extract_image_text=_resolve_boolean(global_extract_image, account_extract_image),
        extract_other_text=_resolve_boolean(global_extract_other, account_extract_other),
    )


def _resolve_boolean(global_val: bool, account_val: Optional[bool]) -> bool:
    """
    Resolve boolean with global stronger negative.

    If global=False (disabled), account cannot enable it (False wins).
    If global=True, account can set to False to disable.
    """
    if account_val is None:
        return global_val
    return global_val and account_val


def _resolve_max_value(global_val: int, account_val: Optional[int]) -> int:
    """
    Resolve max value - global stronger negative means smaller limit wins.
    """
    if account_val is None:
        return global_val
    return min(global_val, account_val)


def should_extract_text(config: StorageConfig, content_type: str) -> bool:
    """Check if text should be extracted for the given content type."""
    if content_type is None:
        return False

    content_type_lower = content_type.lower()

    if content_type_lower == "application/pdf":
        return config.extract_pdf_text
    if content_type_lower in (
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return config.extract_docx_text
    if content_type_lower.startswith("image/"):
        return config.extract_image_text
    if content_type_lower.startswith("text/"):
        return config.extract_other_text
    if content_type_lower in (
        "application/json",
        "application/xml",
        "application/csv",
        "application/rtf",
    ):
        return config.extract_other_text

    return False
