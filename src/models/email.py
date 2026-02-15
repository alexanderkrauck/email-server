"""Email model for logging processed emails."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class EmailLog(Base):
    """Email log model for tracking processed emails.

    Email body content is stored directly in the database (body_plain, body_html).
    """

    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id"), nullable=False)

    sender = Column(String(500), nullable=False)
    recipient = Column(String(500), nullable=False)
    subject = Column(Text, nullable=True)
    message_id = Column(String(255), unique=True, nullable=False)

    body_plain = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    email_date = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, default=func.now())

    attachment_count = Column(Integer, default=0)

    attachments = relationship("EmailAttachment", back_populates="email_log", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<EmailLog(sender='{self.sender}', subject='{self.subject}', processed_at='{self.processed_at}')>"
