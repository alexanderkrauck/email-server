"""Base model for SQLAlchemy."""

from sqlalchemy.ext.declarative import declarative_base

# Single declarative base for all models
Base = declarative_base()
