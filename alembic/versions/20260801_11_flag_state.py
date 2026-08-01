"""Normalise provider flags into filterable read state.

IMAP records that a message was read ("\\Seen"); the Gmail API records that it
was not ("UNREAD"). Both were stored verbatim in one text column, so neither was
filterable and the two could not be compared. Backfill derives the tri-state
form from whatever is already there; rows that never had flags stay NULL, which
is the honest answer and the one search reports as a warning.

Revision ID: 20260801_11
Revises: 20260731_10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260801_11"
down_revision = "20260731_10"
branch_labels = None
depends_on = None

COLUMNS = ("is_unread", "is_flagged", "is_answered")

# Gmail stores json.dumps(sorted(labelIds)), so a label is always quoted exactly.
# strpos avoids both LIKE's backslash escaping and a jsonb cast that would fail
# on any row a future provider writes in a third shape.
BACKFILL = r"""
UPDATE email_logs SET
    is_unread = CASE
        WHEN flags IS NULL THEN NULL
        WHEN left(btrim(flags), 1) = '[' THEN strpos(flags, '"UNREAD"') > 0
        ELSE strpos(lower(' ' || flags || ' '), ' \seen ') = 0
    END,
    is_flagged = CASE
        WHEN flags IS NULL THEN NULL
        WHEN left(btrim(flags), 1) = '[' THEN strpos(flags, '"STARRED"') > 0
        ELSE strpos(lower(' ' || flags || ' '), ' \flagged ') > 0
    END,
    is_answered = CASE
        WHEN flags IS NULL THEN NULL
        -- Gmail publishes no answered label. NULL, not false.
        WHEN left(btrim(flags), 1) = '[' THEN NULL
        ELSE strpos(lower(' ' || flags || ' '), ' \answered ') > 0
    END
WHERE flags IS NOT NULL
"""


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("email_logs")}
    for name in COLUMNS:
        # A fresh install already has these from Base.metadata.create_all.
        if name not in existing:
            op.add_column("email_logs", sa.Column(name, sa.Boolean(), nullable=True))

    op.execute(BACKFILL)

    if bind.dialect.name != "postgresql":
        return
    # Partial indexes: "unread" and "flagged" are the small, interesting sets, and
    # both are almost always asked for newest-first within one account.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_logs_unread ON email_logs "
        "(smtp_config_id, email_date DESC) WHERE is_unread"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_logs_flagged ON email_logs "
        "(smtp_config_id, email_date DESC) WHERE is_flagged"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_email_logs_flagged")
    op.execute("DROP INDEX IF EXISTS ix_email_logs_unread")
    for name in COLUMNS:
        op.drop_column("email_logs", name)
