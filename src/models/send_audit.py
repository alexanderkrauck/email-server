"""Append-only record of outbound send attempts."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from .base import Base


class SendAudit(Base):
    __tablename__ = "send_audits"
    __table_args__ = (UniqueConstraint("owner_user_id", "idempotency_key", name="uq_send_owner_idempotency"),)

    id = Column(Integer, primary_key=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    smtp_config_id = Column(Integer, ForeignKey("smtp_configs.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    recipients_json = Column(Text, nullable=False)
    subject = Column(Text, nullable=False)
    request_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    provider_message_id = Column(String(768), nullable=True)
    provider_response = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
