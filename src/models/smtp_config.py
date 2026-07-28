"""Mail account configuration model."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class SMTPConfig(Base):
    """An IMAP/SMTP mailbox owned by one application user."""

    __tablename__ = "smtp_configs"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_mail_account_owner_name"),)

    id = Column(Integer, primary_key=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(32), nullable=False, default="imap")
    auth_type = Column(String(32), nullable=False, default="password")
    provider_account_id = Column(String(255), nullable=True)
    name = Column(String(255), nullable=False)
    account_name = Column(String(255), nullable=True)  # For organizing storage by account
    host = Column(String(255), nullable=False)  # IMAP host
    port = Column(Integer, nullable=False, default=993)  # IMAP port
    smtp_host = Column(String(255), nullable=True)  # SMTP host (if different from IMAP)
    smtp_port = Column(Integer, nullable=False, default=465)  # SMTP port
    username = Column(String(255), nullable=False)
    credential_ciphertext = Column(Text, nullable=False)
    # IMAP settings
    imap_use_ssl = Column(Boolean, default=True)  # IMAP typically uses SSL on 993
    imap_use_tls = Column(Boolean, default=False)
    # SMTP settings
    smtp_use_ssl = Column(Boolean, default=False)  # SMTP on 587 uses TLS, on 465 uses SSL
    smtp_use_tls = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)

    # Storage overrides (NULL = use global setting)
    # Global stronger negative: if global=False, account can't override to True
    store_text_only_override = Column(Boolean, nullable=True)
    max_attachment_size_override = Column(Integer, nullable=True)
    extract_pdf_text_override = Column(Boolean, nullable=True)
    extract_docx_text_override = Column(Boolean, nullable=True)
    extract_image_text_override = Column(Boolean, nullable=True)
    extract_other_text_override = Column(Boolean, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Stats
    last_check = Column(DateTime, nullable=True)
    total_emails_processed = Column(Integer, default=0)
    sync_locked_at = Column(DateTime, nullable=True)
    sync_lock_token = Column(String(64), nullable=True)
    sync_lock_expires_at = Column(DateTime, nullable=True)
    sync_state = Column(String(32), nullable=False, default="pending")
    backfill_complete = Column(Boolean, nullable=False, default=False)
    backfill_processed = Column(Integer, nullable=False, default=0)
    backfill_total = Column(Integer, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_reconciled_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    retry_at = Column(DateTime, nullable=True)
    provider_sync_token = Column(String(255), nullable=True)
    sync_page_token = Column(Text, nullable=True)
    initial_sync_complete = Column(Boolean, nullable=False, default=False)
    sync_generation = Column(Integer, nullable=False, default=0)

    owner = relationship("User", back_populates="mail_accounts")
    emails = relationship("EmailLog", back_populates="mail_account")
    sync_cursors = relationship("MailSyncCursor", back_populates="mail_account", cascade="all, delete-orphan")

    @property
    def password(self) -> str:
        """Decrypt the provider credential only at the connection boundary."""
        from src.security.crypto import decrypt_secret

        return decrypt_secret(self.credential_ciphertext)

    @password.setter
    def password(self, value: str) -> None:
        from src.security.crypto import encrypt_secret

        self.credential_ciphertext = encrypt_secret(value)

    def __repr__(self):
        return f"<SMTPConfig(name='{self.name}', host='{self.host}', enabled={self.enabled})>"

    def dict(self):
        """Convert to dictionary with proper datetime serialization."""
        return {
            "id": self.id,
            "provider": self.provider,
            "auth_type": self.auth_type,
            "name": self.name,
            "account_name": self.account_name,
            "host": self.host,
            "port": self.port,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "username": self.username,
            "imap_use_ssl": self.imap_use_ssl,
            "imap_use_tls": self.imap_use_tls,
            "smtp_use_ssl": self.smtp_use_ssl,
            "smtp_use_tls": self.smtp_use_tls,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
            "last_check": self.last_check.isoformat() if self.last_check else "",
            "sync_state": self.sync_state,
            "backfill_complete": self.backfill_complete,
            "backfill_processed": self.backfill_processed,
            "backfill_total": self.backfill_total,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else "",
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else "",
            "last_reconciled_at": self.last_reconciled_at.isoformat()
            if self.last_reconciled_at
            else "",
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "retry_at": self.retry_at.isoformat() if self.retry_at else "",
            "total_emails_processed": self.total_emails_processed,
            "initial_sync_complete": self.initial_sync_complete,
            "store_text_only_override": self.store_text_only_override,
            "max_attachment_size_override": self.max_attachment_size_override,
            "extract_pdf_text_override": self.extract_pdf_text_override,
            "extract_docx_text_override": self.extract_docx_text_override,
            "extract_image_text_override": self.extract_image_text_override,
            "extract_other_text_override": self.extract_other_text_override,
        }

    @staticmethod
    def create_detached(config: "SMTPConfig") -> "SMTPConfig":
        """Create a detached copy of the config for use outside SQLAlchemy session."""

        class DetachedConfig:
            pass

        detached = DetachedConfig()
        detached.id = config.id
        detached.owner_user_id = config.owner_user_id
        detached.provider = config.provider
        detached.auth_type = config.auth_type
        detached.name = config.name
        detached.account_name = config.account_name
        detached.host = config.host
        detached.port = config.port
        detached.smtp_host = config.smtp_host
        detached.smtp_port = config.smtp_port
        detached.username = config.username
        detached.credential_ciphertext = config.credential_ciphertext
        detached.password = config.password if config.auth_type == "password" else ""
        detached.imap_use_ssl = config.imap_use_ssl
        detached.imap_use_tls = config.imap_use_tls
        detached.smtp_use_ssl = config.smtp_use_ssl
        detached.smtp_use_tls = config.smtp_use_tls
        detached.enabled = config.enabled
        detached.sync_state = config.sync_state
        detached.backfill_complete = config.backfill_complete
        detached.backfill_processed = config.backfill_processed
        detached.backfill_total = config.backfill_total
        detached.last_attempt_at = config.last_attempt_at
        detached.last_success_at = config.last_success_at
        detached.last_reconciled_at = config.last_reconciled_at
        detached.last_error_code = config.last_error_code
        detached.last_error_message = config.last_error_message
        detached.consecutive_failures = config.consecutive_failures
        detached.retry_at = config.retry_at
        detached.provider_sync_token = config.provider_sync_token
        detached.sync_page_token = config.sync_page_token
        detached.initial_sync_complete = config.initial_sync_complete
        detached.sync_generation = config.sync_generation
        detached.store_text_only_override = config.store_text_only_override
        detached.max_attachment_size_override = config.max_attachment_size_override
        detached.extract_pdf_text_override = config.extract_pdf_text_override
        detached.extract_docx_text_override = config.extract_docx_text_override
        detached.extract_image_text_override = config.extract_image_text_override
        detached.extract_other_text_override = config.extract_other_text_override
        detached.sync_cursors = {
            cursor.folder: {
                "uid_validity": cursor.uid_validity,
                "last_uid": cursor.last_uid,
                "backfill_complete": cursor.backfill_complete,
            }
            for cursor in config.sync_cursors
        }
        return detached
