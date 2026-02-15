"""Tests for database connection."""

import pytest
import os


def test_database_engine_creation():
    """Test database engine creation."""
    from src.database.connection import engine
    
    assert engine is not None
    assert "sqlite" in str(engine.url)


def test_get_db_session():
    """Test get_db_session context manager."""
    from src.database.connection import get_db_session
    
    with get_db_session() as db:
        assert db is not None
