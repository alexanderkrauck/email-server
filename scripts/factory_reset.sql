-- Factory Reset SQL for Email Server
-- Wipes all email data while preserving account configurations
--
-- Usage:
--   sqlite3 emailserver.db < factory_reset.sql
--
-- Or run interactively with:
--   sqlite3 emailserver.db
--   .read factory_reset.sql

.print "=== Email Server Factory Reset ==="
.print ""

-- Show current state
.print "Before reset:"
SELECT 'Accounts (will be preserved): ' || COUNT(*) FROM smtp_configs;
SELECT 'Emails (will be deleted): ' || COUNT(*) FROM email_logs;
SELECT 'Attachments (will be deleted): ' || COUNT(*) FROM email_attachments;
.print ""

-- Delete all attachment records
.print "Deleting attachment records..."
DELETE FROM email_attachments;
.print "Done."

-- Delete all email logs
.print "Deleting email logs..."
DELETE FROM email_logs;
.print "Done."

-- Vacuum to reclaim space
.print "Vacuuming database..."
VACUUM;
.print "Done."
.print ""

-- Show after state
.print "After reset:"
SELECT 'Accounts preserved: ' || COUNT(*) FROM smtp_configs;
SELECT 'Emails remaining: ' || COUNT(*) FROM email_logs;
SELECT 'Attachments remaining: ' || COUNT(*) FROM email_attachments;
.print ""
.print "=== Reset Complete ==="
.print "Note: You must manually clean up storage files in the emails/ directory"
