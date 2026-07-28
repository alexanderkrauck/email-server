"""Persistent IMAP folder synchronization cursor."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class MailSyncCursor(Base):
    __tablename__ = "mail_sync_cursors"
    __table_args__ = (UniqueConstraint("smtp_config_id", "folder", name="uq_sync_cursor_account_folder"),)

    id = Column(Integer, primary_key=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    folder = Column(String(500), nullable=False)
    uid_validity = Column(Integer, nullable=True)
    last_uid = Column(Integer, nullable=True)
    backfill_complete = Column(Boolean, nullable=False, default=False)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    mail_account = relationship("SMTPConfig", back_populates="sync_cursors")
