"""Track oversized IMAP messages indexed from headers only.

Revision ID: 20260729_07
Revises: 20260728_06
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_07"
down_revision = "20260728_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_logs",
        sa.Column("provider_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "email_logs",
        sa.Column(
            "content_state",
            sa.String(length=32),
            nullable=False,
            server_default="complete",
        ),
    )
    op.alter_column("email_logs", "content_state", server_default=None)


def downgrade() -> None:
    op.drop_column("email_logs", "content_state")
    op.drop_column("email_logs", "provider_size")
