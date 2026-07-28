"""Alembic migration environment."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.models.base import Base
import src.models  # noqa: F401

config = context.config
target_metadata = Base.metadata
MIGRATION_MANAGED_INDEXES = {
    "ix_email_attachments_text_fts",
    "ix_email_logs_search_fts",
}


def include_object(obj, name, type_, reflected, compare_to):
    """Keep raw PostgreSQL expression indexes under explicit migration control."""
    if type_ == "index" and reflected and name in MIGRATION_MANAGED_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
