"""Where a message currently sits. Identity lives on EmailLog; location lives here."""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from .base import Base


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class MessagePlacement(Base):
    __tablename__ = "message_placements"
    __table_args__ = (
        UniqueConstraint("email_log_id", "folder", name="uq_placement_message_folder"),
    )

    id = Column(Integer, primary_key=True)
    email_log_id = Column(
        Integer, ForeignKey("email_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder = Column(String(500), nullable=False, index=True)
    uid = Column(Integer, nullable=True)
    uid_validity = Column(Integer, nullable=True)
    # Set in Python: PostgreSQL now() is transaction-start granular, so a server
    # default would make every placement written in one sync indistinguishable by
    # time, and attachment refetch could then prefer a Trash placement.
    seen_at = Column(DateTime, nullable=False, default=_now)
