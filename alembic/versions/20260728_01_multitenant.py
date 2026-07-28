"""Add users, tenant ownership, sync state, and provenance.

Revision ID: 20260728_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260728_01"
down_revision = None
branch_labels = None
depends_on = None


def _create_fresh_schema() -> None:
    from src.models.base import Base
    import src.models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind())


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("smtp_configs"):
        _create_fresh_schema()
        _create_search_index()
        return

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("google_sub", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO users (google_sub, email, status) "
        "VALUES ('development-owner', 'local-owner@example.invalid', 'active')"
    )

    with op.batch_alter_table("smtp_configs") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("provider", sa.String(32), nullable=True, server_default="imap"))
        batch.add_column(sa.Column("auth_type", sa.String(32), nullable=True, server_default="password"))
        batch.add_column(sa.Column("provider_account_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("sync_locked_at", sa.DateTime(), nullable=True))
        batch.alter_column("password", new_column_name="credential_ciphertext")
        batch.drop_constraint("smtp_configs_name_key", type_="unique")
    op.execute("UPDATE smtp_configs SET owner_user_id = 1, auth_type = 'password', provider = CASE WHEN host ILIKE '%gmail%' THEN 'gmail' WHEN host ILIKE '%zoho%' THEN 'zoho' ELSE 'imap' END")
    with op.batch_alter_table("smtp_configs") as batch:
        batch.alter_column("owner_user_id", nullable=False)
        batch.alter_column("provider", nullable=False)
        batch.alter_column("auth_type", nullable=False)
        batch.create_foreign_key("fk_smtp_owner", "users", ["owner_user_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_mail_account_owner_name", ["owner_user_id", "name"])
        batch.create_index("ix_smtp_configs_owner_user_id", ["owner_user_id"])

    with op.batch_alter_table("email_logs") as batch:
        batch.add_column(sa.Column("provider_message_id", sa.String(768), nullable=True))
        batch.add_column(sa.Column("provider_thread_id", sa.String(768), nullable=True))
        batch.add_column(sa.Column("to_addresses", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cc_addresses", sa.Text(), nullable=True))
        batch.add_column(sa.Column("bcc_addresses", sa.Text(), nullable=True))
        batch.add_column(sa.Column("folder", sa.String(500), nullable=True))
        batch.add_column(sa.Column("imap_uid", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("uid_validity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("flags", sa.Text(), nullable=True))
        batch.add_column(sa.Column("in_reply_to", sa.String(255), nullable=True))
        batch.add_column(sa.Column("references", sa.Text(), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.drop_constraint("email_logs_message_id_key", type_="unique")
    op.execute("UPDATE email_logs SET provider_message_id = message_id")
    with op.batch_alter_table("email_logs") as batch:
        batch.alter_column("provider_message_id", nullable=False)
        batch.create_unique_constraint(
            "uq_email_account_provider_message", ["smtp_config_id", "provider_message_id"]
        )
        batch.create_index("ix_email_logs_message_id", ["message_id"])

    with op.batch_alter_table("email_attachments") as batch:
        batch.add_column(sa.Column("claimed_content_type", sa.String(100)))
        batch.add_column(sa.Column("detected_content_type", sa.String(100)))
        batch.add_column(sa.Column("provider_attachment_id", sa.String(500)))
        batch.add_column(sa.Column("part_index", sa.Integer()))
        batch.add_column(sa.Column("sha256", sa.String(64)))
        batch.add_column(sa.Column("extraction_state", sa.String(32), nullable=True, server_default="complete"))
        batch.add_column(sa.Column("extraction_error", sa.Text()))
        batch.add_column(sa.Column("extractor_version", sa.String(32)))
    op.execute(
        "UPDATE email_attachments SET claimed_content_type = content_type, "
        "extraction_state = CASE WHEN text_content IS NULL THEN 'skipped' ELSE 'complete' END, "
        "extractor_version = 'legacy'"
    )
    with op.batch_alter_table("email_attachments") as batch:
        batch.alter_column("extraction_state", nullable=False)

    op.create_table(
        "mail_sync_cursors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("smtp_config_id", sa.Integer(), sa.ForeignKey("smtp_configs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("folder", sa.String(500), nullable=False),
        sa.Column("uid_validity", sa.Integer()),
        sa.Column("last_uid", sa.Integer()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("smtp_config_id", "folder", name="uq_sync_cursor_account_folder"),
    )
    op.create_index("ix_mail_sync_cursors_smtp_config_id", "mail_sync_cursors", ["smtp_config_id"])

    op.create_table(
        "send_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("smtp_config_id", sa.Integer(), sa.ForeignKey("smtp_configs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("recipients_json", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider_message_id", sa.String(768)),
        sa.Column("provider_response", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_send_owner_idempotency"),
    )
    op.create_index("ix_send_audits_owner_user_id", "send_audits", ["owner_user_id"])
    _create_search_index()


def _create_search_index() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_logs_search_fts ON email_logs USING gin "
        "(to_tsvector('simple', coalesce(sender, '') || ' ' || coalesce(recipient, '') || ' ' || "
        "coalesce(subject, '') || ' ' || coalesce(body_plain, '')))"
    )


def downgrade() -> None:
    raise RuntimeError("This data-preserving migration is intentionally irreversible")
