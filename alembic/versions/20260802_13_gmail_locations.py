"""Give Gmail API messages a location instead of the word "gmail".

Every message synced through the Gmail API was filed in a placement whose folder
was the literal string "gmail". It told search nothing -- scoping to INBOX
matched none of 2,047 inbox messages -- and it told the write path nothing, so
every write refused for want of somewhere to address. Worse, "gmail" matches no
entry in excluded_folder_suffixes, so messages sitting in Gmail's Spam ranked as
live mail.

Gmail has no folders, so one location is projected from the labels already
stored on each row, by the same precedence as src/email/gmail_labels.py. The
names are chosen so TRASH and SPAM lowercase into excluded_folder_suffixes.

Revision ID: 20260802_13
Revises: 20260801_12
"""

from alembic import op

revision = "20260802_13"
down_revision = "20260801_12"
branch_labels = None
depends_on = None

# Mirrors gmail_labels.location_for. strpos rather than a jsonb cast, so a row
# whose flags a future provider writes in another shape cannot fail the upgrade.
LOCATION = """
    CASE
        WHEN strpos(e.flags, '"TRASH"') > 0 THEN 'TRASH'
        WHEN strpos(e.flags, '"SPAM"') > 0 THEN 'SPAM'
        WHEN strpos(e.flags, '"DRAFT"') > 0 THEN 'DRAFT'
        WHEN strpos(e.flags, '"INBOX"') > 0 THEN 'INBOX'
        WHEN strpos(e.flags, '"SENT"') > 0 THEN 'SENT'
        ELSE 'ARCHIVE'
    END
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Each such message has exactly one placement today, so rewriting the folder
    # cannot collide with uq_placement_message_folder.
    op.execute(
        f"""
        UPDATE message_placements p
        SET folder = {LOCATION}
        FROM email_logs e
        JOIN smtp_configs c ON c.id = e.smtp_config_id
        WHERE p.email_log_id = e.id
          AND c.provider = 'gmail'
          AND c.auth_type = 'oauth2'
          AND p.folder = 'gmail'
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        UPDATE message_placements p
        SET folder = 'gmail'
        FROM email_logs e
        JOIN smtp_configs c ON c.id = e.smtp_config_id
        WHERE p.email_log_id = e.id
          AND c.provider = 'gmail'
          AND c.auth_type = 'oauth2'
        """
    )
