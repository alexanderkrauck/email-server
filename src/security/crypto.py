"""Persistent encryption for mailbox credentials and signing secrets."""

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from src.config import data_path, settings

_ENCRYPTED_PREFIX = "enc:v1:"


def _private_file(path: Path, length: int = 48) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="ascii").strip()

    value = secrets.token_urlsafe(length)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value)
    return value


def persistent_secret(configured: str, filename: str) -> str:
    """Use an environment secret or create a mode-0600 persistent secret."""
    return configured or _private_file(data_path(filename))


def _fernet() -> Fernet:
    raw_key = persistent_secret(settings.credential_encryption_key, "credential.key")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if value.startswith(_ENCRYPTED_PREFIX):
        return value
    encrypted = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_ENCRYPTED_PREFIX}{encrypted}"


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_ENCRYPTED_PREFIX):
        # Transitional support: startup encrypts legacy rows immediately after migration.
        return value
    try:
        return _fernet().decrypt(value.removeprefix(_ENCRYPTED_PREFIX).encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Mailbox credential cannot be decrypted with the configured key") from exc


def rotate_plaintext_credentials() -> int:
    """Encrypt credentials migrated from the legacy plaintext column."""
    from src.database.connection import SessionLocal
    from src.models.smtp_config import SMTPConfig

    updated = 0
    with SessionLocal.begin() as db:
        for account in db.query(SMTPConfig).all():
            if not account.credential_ciphertext.startswith(_ENCRYPTED_PREFIX):
                account.credential_ciphertext = encrypt_secret(account.credential_ciphertext)
                updated += 1
    return updated
