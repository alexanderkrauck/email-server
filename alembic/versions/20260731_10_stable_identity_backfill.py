"""Move folder and UID out of message identity and into placements.

Revision ID: 20260731_10
Revises: 20260731_09

IMAP identities were "{folder}:{uid_validity}:{uid}", so the same message filed in
two folders became two rows and a move read as a delete plus an unrelated arrival.

Rehearsed against a restored 59,325-message dump: 6,774 rows merge away leaving
52,551 messages and 36,612 placements, with zero duplicates, orphans or
attachment_count mismatches.
"""

from alembic import op

revision = "20260731_10"
down_revision = "20260731_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.exec_driver_sql("SELECT count(*) FROM email_logs").scalar() == 0:
        return

    # The rewrite below is deliberately non-unique until the merge resolves it, so
    # the constraint has to come off first or the migration aborts on its first
    # UPDATE. init_database() runs alembic at startup, so that would stop the server.
    op.execute("ALTER TABLE email_logs DROP CONSTRAINT IF EXISTS uq_email_account_provider_message")

    # 1. Record where every message currently sits.
    op.execute(
        """
        INSERT INTO message_placements (email_log_id, folder, uid, uid_validity, seen_at)
        SELECT id, folder, imap_uid, uid_validity, now()
        FROM email_logs WHERE folder IS NOT NULL
        ON CONFLICT (email_log_id, folder) DO NOTHING
        """
    )

    # 2. Folder-independent identity. The discriminator is the SYNC PATH, not the
    #    provider name: one account is provider='gmail' with auth_type='password'
    #    and therefore syncs over IMAP.
    op.execute(
        r"""
        UPDATE email_logs e
        SET provider_message_id = lower(regexp_replace(e.message_id, '^\s+|\s+$', '', 'g'))
        FROM smtp_configs c
        WHERE c.id = e.smtp_config_id
          AND NOT (c.provider = 'gmail' AND c.auth_type = 'oauth2')
          AND e.message_id IS NOT NULL
          AND regexp_replace(e.message_id, '^\s+|\s+$', '', 'g') <> ''
          AND e.message_id !~ '^uid_[0-9]+_[0-9]+$'
        """
    )

    # 3. No trustworthy Message-ID: keep a location identity, explicitly marked.
    #    A sender+subject+date hash would collapse those rows and silently merge
    #    genuinely distinct messages.
    op.execute(
        r"""
        UPDATE email_logs e
        SET provider_message_id = 'loc:' || coalesce(e.folder,'') || ':'
                                  || coalesce(e.uid_validity,0) || ':' || coalesce(e.imap_uid,0)
        FROM smtp_configs c
        WHERE c.id = e.smtp_config_id
          AND NOT (c.provider = 'gmail' AND c.auth_type = 'oauth2')
          AND (e.message_id IS NULL
               OR regexp_replace(e.message_id, '^\s+|\s+$', '', 'g') = ''
               OR e.message_id ~ '^uid_[0-9]+_[0-9]+$')
        """
    )

    # 4. Merge rows that now share an identity. Lowest id wins: it is the oldest row
    #    and owns the extracted attachment text.
    op.execute(
        """
        CREATE TEMP TABLE merge_map AS
        SELECT e.id AS loser_id, canon.keep_id
        FROM email_logs e
        JOIN (SELECT smtp_config_id, provider_message_id, min(id) AS keep_id
              FROM email_logs GROUP BY 1,2 HAVING count(*) > 1) canon
          ON canon.smtp_config_id = e.smtp_config_id
         AND canon.provider_message_id = e.provider_message_id
        WHERE e.id <> canon.keep_id
        """
    )
    # DISTINCT ON, not NOT EXISTS: the guard is evaluated against a pre-statement
    # snapshot, so two losers of the same winner in the same folder both pass it.
    op.execute(
        """
        WITH movable AS (
          SELECT DISTINCT ON (m.keep_id, pl.folder) pl.id AS placement_id, m.keep_id
          FROM message_placements pl JOIN merge_map m ON m.loser_id = pl.email_log_id
          WHERE NOT EXISTS (SELECT 1 FROM message_placements q
                            WHERE q.email_log_id = m.keep_id AND q.folder = pl.folder)
          ORDER BY m.keep_id, pl.folder, pl.id
        )
        UPDATE message_placements p SET email_log_id = mv.keep_id
        FROM movable mv WHERE p.id = mv.placement_id
        """
    )
    op.execute("DELETE FROM message_placements p USING merge_map m WHERE p.email_log_id = m.loser_id")
    op.execute(
        """
        WITH movable AS (
          SELECT DISTINCT ON (m.keep_id, a.filename, a.sha256) a.id AS att_id, m.keep_id
          FROM email_attachments a JOIN merge_map m ON m.loser_id = a.email_log_id
          WHERE NOT EXISTS (SELECT 1 FROM email_attachments b
                            WHERE b.email_log_id = m.keep_id
                              AND b.filename IS NOT DISTINCT FROM a.filename
                              AND b.sha256 IS NOT DISTINCT FROM a.sha256)
          ORDER BY m.keep_id, a.filename, a.sha256, a.id
        )
        UPDATE email_attachments x SET email_log_id = mv.keep_id
        FROM movable mv WHERE x.id = mv.att_id
        """
    )
    op.execute("DELETE FROM email_attachments a USING merge_map m WHERE a.email_log_id = m.loser_id")
    op.execute(
        """
        WITH movable AS (
          SELECT DISTINCT ON (m.keep_id, p.role, p.email) p.id AS part_id, m.keep_id
          FROM mail_participants p JOIN merge_map m ON m.loser_id = p.email_log_id
          WHERE NOT EXISTS (SELECT 1 FROM mail_participants q
                            WHERE q.email_log_id = m.keep_id AND q.role = p.role AND q.email = p.email)
          ORDER BY m.keep_id, p.role, p.email, p.id
        )
        UPDATE mail_participants x SET email_log_id = mv.keep_id
        FROM movable mv WHERE x.id = mv.part_id
        """
    )
    op.execute("DELETE FROM mail_participants p USING merge_map m WHERE p.email_log_id = m.loser_id")
    op.execute(
        "UPDATE send_audits s SET reply_to_email_id = m.keep_id "
        "FROM merge_map m WHERE s.reply_to_email_id = m.loser_id"
    )
    op.execute("DELETE FROM email_logs e USING merge_map m WHERE e.id = m.loser_id")

    # 5. The merge moves attachments between rows, so the denormalised count must
    #    follow or has_attachments search silently misses them.
    op.execute(
        """
        UPDATE email_logs e SET attachment_count = c.n
        FROM (SELECT email_log_id, count(*) n FROM email_attachments GROUP BY 1) c
        WHERE c.email_log_id = e.id AND e.attachment_count IS DISTINCT FROM c.n
        """
    )
    op.execute(
        "UPDATE email_logs e SET attachment_count = 0 WHERE e.attachment_count <> 0 "
        "AND NOT EXISTS (SELECT 1 FROM email_attachments a WHERE a.email_log_id = e.id)"
    )
    # A merged message demonstrably exists upstream.
    op.execute(
        "UPDATE email_logs e SET deleted_at = NULL WHERE e.deleted_at IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM message_placements p WHERE p.email_log_id = e.id)"
    )

    op.execute(
        "ALTER TABLE email_logs ADD CONSTRAINT uq_email_account_provider_message "
        "UNIQUE (smtp_config_id, provider_message_id)"
    )
    op.execute("DROP TABLE merge_map")


def downgrade() -> None:
    raise RuntimeError("This data-merging migration is intentionally irreversible")
