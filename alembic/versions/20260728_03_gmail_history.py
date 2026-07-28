"""Add resumable Gmail API synchronization state.

Revision ID: 20260728_03
Revises: 20260728_02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260728_03"
down_revision = "20260728_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    account_columns = {
        column["name"] for column in inspector.get_columns("smtp_configs")
    }
    missing_account_columns = {
        "provider_sync_token",
        "sync_page_token",
        "initial_sync_complete",
        "sync_generation",
    } - account_columns
    if missing_account_columns:
        with op.batch_alter_table("smtp_configs") as batch:
            if "provider_sync_token" in missing_account_columns:
                batch.add_column(
                    sa.Column("provider_sync_token", sa.String(255), nullable=True)
                )
            if "sync_page_token" in missing_account_columns:
                batch.add_column(sa.Column("sync_page_token", sa.Text(), nullable=True))
            if "initial_sync_complete" in missing_account_columns:
                batch.add_column(
                    sa.Column(
                        "initial_sync_complete",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "sync_generation" in missing_account_columns:
                batch.add_column(
                    sa.Column(
                        "sync_generation",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )

    email_columns = {column["name"] for column in inspector.get_columns("email_logs")}
    if "last_seen_sync_generation" not in email_columns:
        with op.batch_alter_table("email_logs") as batch:
            batch.add_column(
                sa.Column("last_seen_sync_generation", sa.Integer(), nullable=True)
            )


def downgrade() -> None:
    with op.batch_alter_table("email_logs") as batch:
        batch.drop_column("last_seen_sync_generation")
    with op.batch_alter_table("smtp_configs") as batch:
        batch.drop_column("sync_generation")
        batch.drop_column("initial_sync_complete")
        batch.drop_column("sync_page_token")
        batch.drop_column("provider_sync_token")
