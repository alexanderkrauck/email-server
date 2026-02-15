-- Factory Reset SQL for Email Server (PostgreSQL)
-- Wipes all email data while preserving account configurations
--
-- Usage:
--   psql $DATABASE_URL -f factory_reset.sql
--
-- Or run interactively:
--   psql $DATABASE_URL
--   \i factory_reset.sql

\echo '=== Email Server Factory Reset ==='
\echo ''

-- Show current state
\echo 'Before reset:'
SELECT 'Accounts (will be preserved): ' || COUNT(*) FROM smtp_configs;
SELECT 'Emails (will be deleted): ' || COUNT(*) FROM email_logs;
SELECT 'Attachments (will be deleted): ' || COUNT(*) FROM email_attachments;
\echo ''

-- Truncate all email data (CASCADE handles foreign keys)
\echo 'Truncating email data...'
TRUNCATE TABLE email_attachments CASCADE;
TRUNCATE TABLE email_logs CASCADE;
TRUNCATE TABLE email_status CASCADE;
\echo 'Done.'

-- Vacuum to reclaim space
\echo 'Vacuuming database...'
VACUUM FULL;
\echo 'Done.'
\echo ''

-- Show after state
\echo 'After reset:'
SELECT 'Accounts preserved: ' || COUNT(*) FROM smtp_configs;
SELECT 'Emails remaining: ' || COUNT(*) FROM email_logs;
SELECT 'Attachments remaining: ' || COUNT(*) FROM email_attachments;
\echo ''
\echo '=== Reset Complete ==='
