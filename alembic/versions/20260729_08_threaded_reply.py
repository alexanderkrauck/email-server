"""Record the source message for threaded replies.

Revision ID: 20260729_08
Revises: 20260729_07
"""

import sqlalchemy as sa
from alembic import op

revision = "20260729_08"
down_revision = "20260729_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "send_audits",
        sa.Column("reply_to_email_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_send_audit_reply_email",
        "send_audits",
        "email_logs",
        ["reply_to_email_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_send_audit_reply_email",
        "send_audits",
        type_="foreignkey",
    )
    op.drop_column("send_audits", "reply_to_email_id")
