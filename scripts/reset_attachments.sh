#!/bin/bash
# Reset attachment pipeline for Krauck Systems
# Deletes orphaned attachment records so they'll be re-fetched with the new code

set -e

echo "=== Attachment Pipeline Reset for Krauck Systems ==="
echo ""

DB_PATH="/app/data/emailserver.db"

# Check if running in container or host
if [ -f "/app/data/emailserver.db" ]; then
    DB_PATH="/app/data/emailserver.db"
elif [ -f "/root/goedlike/email-server/data/emailserver.db" ]; then
    DB_PATH="/root/goedlike/email-server/data/emailserver.db"
else
    echo "ERROR: Cannot find emailserver.db"
    exit 1
fi

echo "Using database: $DB_PATH"
echo ""

# Get Krauck Systems ID
KRAUCK_ID=$(sqlite3 "$DB_PATH" "SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1;")

if [ -z "$KRAUCK_ID" ]; then
    echo "ERROR: Krauck Systems config not found"
    exit 1
fi

echo "Krauck Systems config ID: $KRAUCK_ID"
echo ""

# Show current stats
echo "Current attachment stats:"
sqlite3 "$DB_PATH" <<EOF
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN file_path IS NOT NULL THEN 1 END) as with_binary,
    COUNT(CASE WHEN text_file_path IS NOT NULL THEN 1 END) as with_text
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = $KRAUCK_ID;
EOF
echo ""

# Show breakdown by type
echo "Attachments by type:"
sqlite3 "$DB_PATH" <<EOF
SELECT 
    CASE 
        WHEN content_type LIKE '%pdf%' THEN 'PDF'
        WHEN content_type LIKE '%doc%' THEN 'DOC/DOCX'
        WHEN content_type LIKE '%image%' THEN 'Image'
        ELSE content_type
    END as type,
    COUNT(*) as count
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = $KRAUCK_ID
GROUP BY type;
EOF
echo ""

echo "WARNING: This will DELETE all attachment records for Krauck Systems"
echo "that don't have binary files saved. They will be re-fetched on next sync."
echo ""
read -p "Continue? Type 'yes' to proceed: " confirm

if [ "$confirm" != "yes" ]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo "Deleting orphaned attachment records..."

# Delete attachments without file_path
sqlite3 "$DB_PATH" <<EOF
DELETE FROM email_attachments 
WHERE email_log_id IN (
    SELECT id FROM email_logs WHERE smtp_config_id = $KRAUCK_ID
)
AND (file_path IS NULL OR file_path = '');
EOF

echo "Done."
echo ""

# Reset attachment_count on email_logs to 0 for affected emails
sqlite3 "$DB_PATH" <<EOF
UPDATE email_logs 
SET attachment_count = (
    SELECT COUNT(*) FROM email_attachments WHERE email_log_id = email_logs.id
)
WHERE smtp_config_id = $KRAUCK_ID;
EOF

echo "Updated attachment counts on email logs."
echo ""

# Show new stats
echo "New attachment stats:"
sqlite3 "$DB_PATH" <<EOF
SELECT 
    COUNT(*) as remaining_attachments
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = $KRAUCK_ID;
EOF

echo ""
echo "=== Next Steps ==="
echo "1. Restart the email-server container to trigger a fresh sync"
echo "2. Or wait for the next scheduled sync"
echo "3. New attachments will be saved with binary files and text extraction"
echo ""
