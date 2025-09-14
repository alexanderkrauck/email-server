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
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=993)
    username = Column(String(255), nullable=False)
    password = Column(Text, nullable=False)  # Should be encrypted in production
    use_tls = Column(Boolean, default=True)
    use_ssl = Column(Boolean, default=False)
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
            'username': self.username,
            'use_tls': self.use_tls,
            'use_ssl': self.use_ssl,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else "",
            'updated_at': self.updated_at.isoformat() if self.updated_at else "",
            'last_check': self.last_check.isoformat() if self.last_check else "",
            'total_emails_processed': self.total_emails_processed
        }