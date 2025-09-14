"""Email attachment model."""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, LargeBinary
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .base import Base


class EmailAttachment(Base):
    """Email attachment model for storing attachment metadata and data."""

    __tablename__ = "email_attachments"

    id = Column(Integer, primary_key=True)
    email_log_id = Column(Integer, ForeignKey("email_logs.id"), nullable=False)

    # Attachment metadata
    filename = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=True)
    content_id = Column(String(255), nullable=True)  # For inline attachments
    size = Column(Integer, default=0)

    # File storage
    file_path = Column(String(1000), nullable=True)  # Path to saved file
    content_data = Column(LargeBinary, nullable=True)  # Small attachments stored inline

    # Timestamps
    created_at = Column(DateTime, default=func.now())

    # Relationship
    email_log = relationship("EmailLog", back_populates="attachments")

    def __repr__(self):
        return f"<EmailAttachment(filename='{self.filename}', size={self.size})>"