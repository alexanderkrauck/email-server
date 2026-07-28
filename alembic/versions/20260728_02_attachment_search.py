"""Index extracted attachment text for lexical search.

Revision ID: 20260728_02
Revises: 20260728_01
"""

from alembic import op

revision = "20260728_02"
down_revision = "20260728_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_attachments_text_fts ON email_attachments USING gin "
        "(to_tsvector('simple', coalesce(text_content, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_email_attachments_text_fts")
