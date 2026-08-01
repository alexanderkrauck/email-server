"""Index message and attachment text under every configured language.

'simple' stems nothing, so a search for "invoice" missed every message that said
"invoices", and "Rechnung" missed "Rechnungen". This adds one combined index per
table holding the union of the same text under each configured search
configuration; identical lexemes collapse, so the union costs far less than one
index per language. The 'simple' indexes stay, because match="exact" is defined
as searching them alone.

The index expression must remain identical to src/services/search_text.py. If it
drifts, search still returns the right answer and does it with a sequential scan
over every stored body.

Revision ID: 20260801_12
Revises: 20260801_11
"""

from alembic import op

from src.services.search_text import (
    ATTACHMENT_DOCUMENT_SQL,
    MESSAGE_DOCUMENT_SQL,
    index_expression,
    index_name,
    text_search_configs,
)

revision = "20260801_12"
down_revision = "20260801_11"
branch_labels = None
depends_on = None

TARGETS = (
    ("ix_email_logs_search_fts", "email_logs", MESSAGE_DOCUMENT_SQL),
    ("ix_email_attachments_text_fts", "email_attachments", ATTACHMENT_DOCUMENT_SQL),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    configs = text_search_configs()
    if len(configs) < 2:
        # Only 'simple' is configured; the existing indexes already cover it.
        return
    for prefix, table, document in TARGETS:
        # The doubled parentheses are required: an index expression that is not a
        # bare column or a single function call has to be parenthesised itself.
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name(prefix, configs)} ON {table} "
            f"USING gin (({index_expression(document, configs)}))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    configs = text_search_configs()
    for prefix, _table, _document in TARGETS:
        op.execute(f"DROP INDEX IF EXISTS {index_name(prefix, configs)}")
