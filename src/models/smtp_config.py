"""SMTP Configuration model."""

from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.sql import func
from datetime import datetime
from .base import Base


class SMTPConfig(Base):
    """SMTP server configuration model."""

    __tablename__ = "smtp_configs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    account_name = Column(String(255), nullable=True)  # For organizing storage by account
    host = Column(String(255), nullable=False)  # IMAP host
    port = Column(Integer, nullable=False, default=993)  # IMAP port
    smtp_host = Column(String(255), nullable=True)  # SMTP host (if different from IMAP)
    smtp_port = Column(Integer, nullable=False, default=465)  # SMTP port
    username = Column(String(255), nullable=False)
    password = Column(Text, nullable=False)  # Should be encrypted in production
    # IMAP settings
    imap_use_ssl = Column(Boolean, default=True)  # IMAP typically uses SSL on 993
    imap_use_tls = Column(Boolean, default=False)
    # SMTP settings
    smtp_use_ssl = Column(Boolean, default=False)  # SMTP on 587 uses TLS, on 465 uses SSL
    smtp_use_tls = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Stats
    last_check = Column(DateTime, nullable=True)
    total_emails_processed = Column(Integer, default=0)

    def __repr__(self):
        return f"<SMTPConfig(name='{self.name}', host='{self.host}', enabled={self.enabled})>"

    def dict(self):
        """Convert to dictionary with proper datetime serialization."""
        return {
            'id': self.id,
            'name': self.name,
            'account_name': self.account_name,
            'host': self.host,
            'port': self.port,
            'smtp_host': self.smtp_host,
            'smtp_port': self.smtp_port,
            'username': self.username,
            'imap_use_ssl': self.imap_use_ssl,
            'imap_use_tls': self.imap_use_tls,
            'smtp_use_ssl': self.smtp_use_ssl,
            'smtp_use_tls': self.smtp_use_tls,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else "",
            'updated_at': self.updated_at.isoformat() if self.updated_at else "",
            'last_check': self.last_check.isoformat() if self.last_check else "",
            'total_emails_processed': self.total_emails_processed
        }