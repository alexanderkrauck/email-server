"""Email model for logging processed emails."""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .base import Base


class EmailLog(Base):
    """Email log model for tracking processed emails."""

    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id"), nullable=False)

    # Email metadata
    sender = Column(String(500), nullable=False)
    recipient = Column(String(500), nullable=False)
    subject = Column(Text, nullable=True)
    message_id = Column(String(255), unique=True, nullable=False)

    # Content
    body_plain = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    # File paths
    log_file_path = Column(String(1000), nullable=True)  # Path to the detailed log file

    # Timestamps
    email_date = Column(DateTime, nullable=True)  # Date from email headers
    processed_at = Column(DateTime, default=func.now())

    # Size info
    content_size = Column(Integer, default=0)  # Size in bytes
    attachment_count = Column(Integer, default=0)

    # Relationships
    attachments = relationship("EmailAttachment", back_populates="email_log", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<EmailLog(sender='{self.sender}', subject='{self.subject}', processed_at='{self.processed_at}')>"