"""Minimal email status tracking model."""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from .base import Base


class EmailStatus(Base):
    """Minimal email status tracking for processed emails."""

    __tablename__ = "email_status"

    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id"), nullable=False)

    # Minimal identification
    message_id = Column(String(255), unique=True, nullable=False, index=True)
    sender = Column(String(500), nullable=False, index=True)
    subject = Column(String(1000), nullable=True)

    # Status tracking
    processed_at = Column(DateTime, default=func.now(), index=True)
    file_path = Column(String(1000), nullable=True)  # Path to markdown file
    has_attachments = Column(Boolean, default=False)
    attachment_count = Column(Integer, default=0)

    # Size for cleanup decisions
    content_size = Column(Integer, default=0)

    def __repr__(self):
        return f"<EmailStatus(message_id='{self.message_id}', sender='{self.sender}')>"