"""Application user identified by an external identity provider."""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    google_sub = Column(String(255), unique=True, nullable=False)
    email = Column(String(320), nullable=False)
    display_name = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    mail_accounts = relationship("SMTPConfig", back_populates="owner", cascade="all, delete-orphan")
