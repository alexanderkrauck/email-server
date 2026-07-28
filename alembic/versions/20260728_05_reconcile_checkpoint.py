"""Persist provider reconciliation checkpoints.

Revision ID: 20260728_05
Revises: 20260728_04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260728_05"
down_revision = "20260728_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"] for column in inspect(bind).get_columns("smtp_configs")
    }
    if "last_reconciled_at" not in columns:
        with op.batch_alter_table("smtp_configs") as batch:
            batch.add_column(sa.Column("last_reconciled_at", sa.DateTime()))
    op.execute(
        """
        UPDATE smtp_configs
        SET last_reconciled_at = last_success_at
        WHERE backfill_complete
          AND last_reconciled_at IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("smtp_configs") as batch:
        batch.drop_column("last_reconciled_at")
