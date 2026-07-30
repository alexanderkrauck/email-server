"""Record the source message for threaded replies.

Revision ID: 20260729_08
Revises: 20260729_07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260729_08"
down_revision = "20260729_07"
branch_labels = None
depends_on = None

FOREIGN_KEY = "fk_send_audit_reply_email"


def upgrade() -> None:
    # A fresh install builds the current schema in revision 01, which already
    # carries this column and an automatically named foreign key.
    inspector = inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("send_audits")}
    if "reply_to_email_id" in columns:
        return

    op.add_column(
        "send_audits",
        sa.Column("reply_to_email_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        FOREIGN_KEY,
        "send_audits",
        "email_logs",
        ["reply_to_email_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    existing = {key["name"] for key in inspector.get_foreign_keys("send_audits")}
    if FOREIGN_KEY in existing:
        op.drop_constraint(FOREIGN_KEY, "send_audits", type_="foreignkey")
    op.drop_column("send_audits", "reply_to_email_id")
