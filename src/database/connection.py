"""Database connection and session management."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from src.config import settings
import logging

logger = logging.getLogger(__name__)

# Create database engine
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
)

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
        logger.error(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def init_database():
    """Initialize database tables."""
    from src.models.base import Base
    from sqlalchemy import text

    # Import all models to ensure they're registered

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Run database migrations
    with get_db_session() as db:
        # Check if smtp_port column exists, if not add it
        try:
            db.execute(text("SELECT smtp_port FROM smtp_configs LIMIT 1"))
        except Exception:
            # Column doesn't exist, add it
            logger.info("Adding smtp_port column to smtp_configs table")
            db.execute(text("ALTER TABLE smtp_configs ADD COLUMN smtp_port INTEGER DEFAULT 587"))
            db.commit()

        # Add new IMAP/SMTP specific SSL/TLS columns
        columns_to_add = [
            ("imap_use_ssl", "BOOLEAN DEFAULT 1"),
            ("imap_use_tls", "BOOLEAN DEFAULT 0"),
            ("smtp_use_ssl", "BOOLEAN DEFAULT 0"),
            ("smtp_use_tls", "BOOLEAN DEFAULT 1"),
            ("smtp_host", "TEXT NULL")
        ]

        for column_name, column_def in columns_to_add:
            try:
                db.execute(text(f"SELECT {column_name} FROM smtp_configs LIMIT 1"))
            except Exception:
                logger.info(f"Adding {column_name} column to smtp_configs table")
                db.execute(text(f"ALTER TABLE smtp_configs ADD COLUMN {column_name} {column_def}"))
                db.commit()

    logger.info("Database initialized successfully")