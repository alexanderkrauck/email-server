"""Retire placements belonging to tombstoned messages.

A tombstoned message is one the provider no longer has, so it has no location.
The IMAP path arrives at a tombstone precisely by losing every placement, so the
invariant holds there for free. The Gmail backfill path tombstones directly, by
noticing a message was absent from a full listing, and left its placement behind
-- so those rows claimed both to be gone and to be filed somewhere.

Revision ID: 20260802_15
Revises: 20260802_14
"""

from alembic import op

revision = "20260802_15"
down_revision = "20260802_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM message_placements p
        USING email_logs e
        WHERE p.email_log_id = e.id AND e.deleted_at IS NOT NULL
        """
    )


def downgrade() -> None:
    # The rows described a location the message did not have.
    raise RuntimeError("This migration removes contradictory rows and is not reversible")
