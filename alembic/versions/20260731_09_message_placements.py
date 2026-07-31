"""Track where each message is filed, separately from its identity.

Revision ID: 20260731_09
Revises: 20260729_08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260731_09"
down_revision = "20260729_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A fresh install already built this table via Base.metadata.create_all.
    if "message_placements" in set(inspect(op.get_bind()).get_table_names()):
        return

    op.create_table(
        "message_placements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "email_log_id",
            sa.Integer(),
            sa.ForeignKey("email_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder", sa.String(500), nullable=False),
        sa.Column("uid", sa.Integer()),
        sa.Column("uid_validity", sa.Integer()),
        sa.Column("seen_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email_log_id", "folder", name="uq_placement_message_folder"),
    )
    op.create_index("ix_message_placements_email_log_id", "message_placements", ["email_log_id"])
    op.create_index("ix_message_placements_folder", "message_placements", ["folder"])


def downgrade() -> None:
    op.drop_table("message_placements")
