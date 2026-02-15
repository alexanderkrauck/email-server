-- Reset Attachment Pipeline for Krauck Systems
-- Run this in sqlite3: sqlite3 emailserver.db < reset_attachments.sql

-- 1. Check current state
.print "=== Before Reset ==="
SELECT 
    COUNT(*) as total_attachments,
    COUNT(CASE WHEN file_path IS NOT NULL THEN 1 END) as with_binary
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1);

-- 2. Delete orphaned attachments (no binary file)
DELETE FROM email_attachments 
WHERE email_log_id IN (
    SELECT id FROM email_logs 
    WHERE smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1)
)
AND (file_path IS NULL OR file_path = '');

-- 3. Update attachment counts
UPDATE email_logs 
SET attachment_count = (
    SELECT COUNT(*) FROM email_attachments WHERE email_log_id = email_logs.id
)
WHERE smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1);

-- 4. Verify
.print "=== After Reset ==="
SELECT 
    COUNT(*) as remaining_attachments
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1);
