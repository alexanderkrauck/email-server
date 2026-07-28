"""Email model for synced messages."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class EmailLog(Base):
    """Email log model for tracking processed emails.

    Email body content is stored directly in the database (body_plain, body_html).
    """

    __tablename__ = "email_logs"
    __table_args__ = (
        UniqueConstraint("smtp_config_id", "provider_message_id", name="uq_email_account_provider_message"),
    )

    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id"), nullable=False)

    sender = Column(String(500), nullable=False)
    recipient = Column(String(500), nullable=False)
    to_addresses = Column(Text, nullable=True)
    cc_addresses = Column(Text, nullable=True)
    bcc_addresses = Column(Text, nullable=True)
    subject = Column(Text, nullable=True)
    provider_message_id = Column(String(768), nullable=False)
    provider_thread_id = Column(String(768), nullable=True)
    message_id = Column(String(255), nullable=False, index=True)
    folder = Column(String(500), nullable=True)
    imap_uid = Column(Integer, nullable=True)
    uid_validity = Column(Integer, nullable=True)
    flags = Column(Text, nullable=True)
    in_reply_to = Column(String(255), nullable=True)
    references = Column(Text, nullable=True)
    content_fingerprint = Column(String(32), nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True)
    last_seen_sync_generation = Column(Integer, nullable=True)

    body_plain = Column(Text, nullable=True)
    body_html = Column(Text, nullable=True)

    email_date = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, default=func.now())

    attachment_count = Column(Integer, default=0)

    attachments = relationship("EmailAttachment", back_populates="email_log", cascade="all, delete-orphan")
    participants = relationship(
        "MailParticipant",
        back_populates="email_log",
        cascade="all, delete-orphan",
    )
    mail_account = relationship("SMTPConfig", back_populates="emails")

    def __repr__(self):
        return f"<EmailLog(sender='{self.sender}', subject='{self.subject}', processed_at='{self.processed_at}')>"
