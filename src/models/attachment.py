"""Email attachment model."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class EmailAttachment(Base):
    """Email attachment model for storing attachment metadata and extracted text."""

    __tablename__ = "email_attachments"

    id = Column(Integer, primary_key=True)
    email_log_id = Column(Integer, ForeignKey("email_logs.id"), nullable=False)

    filename = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=True)
    content_id = Column(String(255), nullable=True)
    size = Column(Integer, default=0)

    text_content = Column(Text, nullable=True)  # Extracted text stored directly in DB

    created_at = Column(DateTime, default=func.now())

    email_log = relationship("EmailLog", back_populates="attachments")

    def __repr__(self):
        return f"<EmailAttachment(filename='{self.filename}', size={self.size})>"
