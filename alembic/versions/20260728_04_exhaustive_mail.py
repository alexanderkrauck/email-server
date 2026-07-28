"""Add exhaustive-search metadata and durable sync health.

Revision ID: 20260728_04
Revises: 20260728_03
"""

import json
from email.utils import getaddresses

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260728_04"
down_revision = "20260728_03"
branch_labels = None
depends_on = None


def _participant_rows(row) -> list[dict]:
    sources = [("from", row.sender), ("to", row.recipient)]
    for role, raw in (
        ("to", row.to_addresses),
        ("cc", row.cc_addresses),
        ("bcc", row.bcc_addresses),
    ):
        if not raw:
            continue
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            values = [raw]
        if isinstance(values, str):
            values = [values]
        sources.extend((role, value) for value in values)

    result = []
    seen = set()
    for role, value in sources:
        for display_name, address in getaddresses([value or ""]):
            email = address.strip().lower()
            if "@" not in email or (role, email) in seen:
                continue
            seen.add((role, email))
            result.append(
                {
                    "email_log_id": row.id,
                    "role": role,
                    "email": email[:320],
                    "domain": email.rsplit("@", 1)[1][:255],
                    "display_name": display_name[:500] or None,
                }
            )
    return result


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    account_columns = {
        column["name"] for column in inspector.get_columns("smtp_configs")
    }
    with op.batch_alter_table("smtp_configs") as batch:
        definitions = {
            "sync_lock_token": sa.Column("sync_lock_token", sa.String(64)),
            "sync_lock_expires_at": sa.Column("sync_lock_expires_at", sa.DateTime()),
            "sync_state": sa.Column(
                "sync_state",
                sa.String(32),
                nullable=False,
                server_default="pending",
            ),
            "backfill_complete": sa.Column(
                "backfill_complete",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            "backfill_processed": sa.Column(
                "backfill_processed",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            "backfill_total": sa.Column("backfill_total", sa.Integer()),
            "last_attempt_at": sa.Column("last_attempt_at", sa.DateTime()),
            "last_success_at": sa.Column("last_success_at", sa.DateTime()),
            "last_error_code": sa.Column("last_error_code", sa.String(64)),
            "last_error_message": sa.Column("last_error_message", sa.Text()),
            "consecutive_failures": sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            "retry_at": sa.Column("retry_at", sa.DateTime()),
        }
        for name, column in definitions.items():
            if name not in account_columns:
                batch.add_column(column)

    cursor_columns = {
        column["name"] for column in inspector.get_columns("mail_sync_cursors")
    }
    if "backfill_complete" not in cursor_columns:
        with op.batch_alter_table("mail_sync_cursors") as batch:
            batch.add_column(
                sa.Column(
                    "backfill_complete",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    email_columns = {
        column["name"] for column in inspector.get_columns("email_logs")
    }
    if "content_fingerprint" not in email_columns:
        with op.batch_alter_table("email_logs") as batch:
            batch.add_column(sa.Column("content_fingerprint", sa.String(32)))
            batch.create_index(
                "ix_email_logs_content_fingerprint",
                ["content_fingerprint"],
            )

    op.execute(
        """
        UPDATE email_logs
        SET content_fingerprint = md5(
            lower(btrim(
                regexp_replace(
                    regexp_replace(
                        coalesce(subject, ''),
                        '^\\s*((re|fw|fwd):|\\[fwd\\])\\s*',
                        '',
                        'i'
                    ),
                    '\\s+',
                    ' ',
                    'g'
                )
            )) || E'\\n' ||
            lower(btrim(regexp_replace(coalesce(body_plain, ''), '\\s+', ' ', 'g')))
        )
        WHERE length(coalesce(body_plain, '')) >= 100
          AND content_fingerprint IS NULL
        """
    )

    table_names = set(inspector.get_table_names())
    if "mail_participants" not in table_names:
        op.create_table(
            "mail_participants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "email_log_id",
                sa.Integer(),
                sa.ForeignKey("email_logs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(8), nullable=False),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("domain", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(500)),
            sa.UniqueConstraint(
                "email_log_id",
                "role",
                "email",
                name="uq_mail_participant_message_role_email",
            ),
        )
        op.create_index(
            "ix_mail_participants_email_log_id",
            "mail_participants",
            ["email_log_id"],
        )
        op.create_index(
            "ix_mail_participants_email",
            "mail_participants",
            ["email"],
        )
        op.create_index(
            "ix_mail_participants_domain",
            "mail_participants",
            ["domain"],
        )

    participants = sa.table(
        "mail_participants",
        sa.column("email_log_id", sa.Integer()),
        sa.column("role", sa.String()),
        sa.column("email", sa.String()),
        sa.column("domain", sa.String()),
        sa.column("display_name", sa.String()),
    )
    result = bind.execute(
        sa.text(
            """
            SELECT id, sender, recipient, to_addresses, cc_addresses, bcc_addresses
            FROM email_logs
            WHERE NOT EXISTS (
                SELECT 1 FROM mail_participants
                WHERE mail_participants.email_log_id = email_logs.id
            )
            ORDER BY id
            """
        )
    )
    while True:
        messages = result.fetchmany(1000)
        if not messages:
            break
        rows = [
            participant
            for message in messages
            for participant in _participant_rows(message)
        ]
        if rows:
            bind.execute(sa.insert(participants), rows)

    op.execute(
        """
        UPDATE mail_sync_cursors
        SET backfill_complete = true
        WHERE last_success_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE smtp_configs AS account
        SET backfill_complete = (
                account.initial_sync_complete
                OR EXISTS (
                    SELECT 1 FROM mail_sync_cursors AS cursor
                    WHERE cursor.smtp_config_id = account.id
                      AND cursor.backfill_complete
                )
            ),
            sync_state = CASE
                WHEN account.enabled AND account.last_check IS NOT NULL THEN 'healthy'
                WHEN account.enabled THEN 'pending'
                ELSE 'disabled'
            END,
            last_success_at = account.last_check
        """
    )


def downgrade() -> None:
    op.drop_table("mail_participants")
    with op.batch_alter_table("email_logs") as batch:
        batch.drop_index("ix_email_logs_content_fingerprint")
        batch.drop_column("content_fingerprint")
    with op.batch_alter_table("mail_sync_cursors") as batch:
        batch.drop_column("backfill_complete")
    with op.batch_alter_table("smtp_configs") as batch:
        for name in (
            "retry_at",
            "consecutive_failures",
            "last_error_message",
            "last_error_code",
            "last_success_at",
            "last_attempt_at",
            "backfill_total",
            "backfill_processed",
            "backfill_complete",
            "sync_state",
            "sync_lock_expires_at",
            "sync_lock_token",
        ):
            batch.drop_column(name)
