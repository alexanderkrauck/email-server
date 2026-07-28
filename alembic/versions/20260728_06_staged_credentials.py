"""Allow mailbox configuration before credential entry.

Revision ID: 20260728_06
Revises: 20260728_05
"""

from alembic import op

revision = "20260728_06"
down_revision = "20260728_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("smtp_configs") as batch:
        batch.alter_column("credential_ciphertext", nullable=True)


def downgrade() -> None:
    op.execute(
        """
        UPDATE smtp_configs
        SET credential_ciphertext = ''
        WHERE credential_ciphertext IS NULL
        """
    )
    with op.batch_alter_table("smtp_configs") as batch:
        batch.alter_column("credential_ciphertext", nullable=False)
