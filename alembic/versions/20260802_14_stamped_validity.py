"""Forget UIDVALIDITY values that were copied from the wrong folder.

A move recorded the destination placement with the *source* folder's UIDVALIDITY,
because parse_copyuid discarded the one COPYUID actually reports. The UID is
right; the numbering space it is labelled with is not, and a wrong label is worse
than no label: it is indistinguishable from the folder having been renumbered,
which is what makes a placement unwritable.

There is no way to recover the true value offline -- only the server knows it --
so the affected rows are set to NULL, which honestly says "not observed". Both
writable_placement and apply_folder_snapshots treat NULL as "cannot judge" and
leave such a placement alone, and the next sync or repair sweep of that folder
records the real value.

Only rows in folders this account has no sync cursor for are touched. A folder
with a cursor has had its validity observed directly, so a mismatch there is a
genuine renumbering and must keep saying so.

Revision ID: 20260802_14
Revises: 20260802_13
"""

from alembic import op

revision = "20260802_14"
down_revision = "20260802_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        UPDATE message_placements p
        SET uid_validity = NULL
        FROM email_logs e
        WHERE p.email_log_id = e.id
          AND p.uid_validity IS NOT NULL
          -- no cursor for this folder: the syncer never observed its validity,
          -- so whatever is stored came from somewhere else
          AND NOT EXISTS (
              SELECT 1 FROM mail_sync_cursors c
              WHERE c.smtp_config_id = e.smtp_config_id AND c.folder = p.folder
          )
          -- and it matches a validity that belongs to a different folder, which
          -- is the fingerprint of having been stamped rather than observed
          AND EXISTS (
              SELECT 1 FROM mail_sync_cursors c
              WHERE c.smtp_config_id = e.smtp_config_id
                AND c.folder <> p.folder
                AND c.uid_validity = p.uid_validity
          )
        """
    )


def downgrade() -> None:
    # The discarded values were wrong; restoring them would restore the defect.
    raise RuntimeError("This migration removes incorrect data and is not reversible")
