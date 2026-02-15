"""Database connection and session management."""

import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings

logger = logging.getLogger(__name__)

# Create database engine with Postgres pool config
engine_kwargs = {"echo": settings.database_echo}
if "postgresql" in settings.database_url:
    engine_kwargs.update(
        {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
        }
    )

engine = create_engine(settings.database_url, **engine_kwargs)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_session():
    """Context manager for database sessions."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        logger.error("Database session error: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


def init_database():
    """Initialize database tables."""
    # Import all models to ensure they're registered
    from src.models import attachment, email, smtp_config  # noqa: F401
    from src.models.base import Base

    # Create all tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")
