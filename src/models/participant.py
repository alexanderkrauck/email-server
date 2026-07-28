"""Normalized message participants for complete address and domain filtering."""

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .base import Base


class MailParticipant(Base):
    __tablename__ = "mail_participants"
    __table_args__ = (
        UniqueConstraint(
            "email_log_id",
            "role",
            "email",
            name="uq_mail_participant_message_role_email",
        ),
    )

    id = Column(Integer, primary_key=True)
    email_log_id = Column(
        Integer,
        ForeignKey("email_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(8), nullable=False)
    email = Column(String(320), nullable=False, index=True)
    domain = Column(String(255), nullable=False, index=True)
    display_name = Column(String(500), nullable=True)

    email_log = relationship("EmailLog", back_populates="participants")
