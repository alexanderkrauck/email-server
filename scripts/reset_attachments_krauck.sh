#!/bin/bash
# Reset attachment extraction pipeline for Krauck Systems
# Excludes accounts/auth - only resets processing state

set -e

echo "=== Attachment Pipeline Reset for Krauck Systems ==="
echo ""

DB_PATH="/app/data/emailserver.db"
DATA_DIR="/app/data/emails"

# Check if DB exists
if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    exit 1
fi

echo "1. Getting Krauck Systems config ID..."
KRAUCK_ID=$(sqlite3 "$DB_PATH" "SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' OR account_name LIKE '%Krauck%' LIMIT 1;")

if [ -z "$KRAUCK_ID" ]; then
    echo "WARNING: Could not find Krauck Systems config. Trying alternative search..."
    KRAUCK_ID=$(sqlite3 "$DB_PATH" "SELECT id FROM smtp_configs WHERE name = 'Krauck Systems' LIMIT 1;")
fi

if [ -z "$KRAUCK_ID" ]; then
    echo "ERROR: Krauck Systems config not found. Available configs:"
    sqlite3 "$DB_PATH" "SELECT id, name, account_name FROM smtp_configs;"
    exit 1
fi

echo "   Found Krauck Systems config ID: $KRAUCK_ID"
echo ""

# Show current stats
echo "2. Current attachment stats for Krauck Systems:"
sqlite3 "$DB_PATH" <<EOF
SELECT 
    COUNT(*) as total_attachments,
    COUNT(CASE WHEN file_path IS NOT NULL THEN 1 END) as with_file_path,
    COUNT(CASE WHEN text_file_path IS NOT NULL THEN 1 END) as with_text,
    COUNT(CASE WHEN file_path IS NULL THEN 1 END) as missing_file
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = $KRAUCK_ID;
EOF
echo ""

# Show attachments by type that need processing
echo "3. Attachments needing processing by content type:"
sqlite3 "$DB_PATH" <<EOF
SELECT 
    CASE 
        WHEN content_type LIKE '%pdf%' THEN 'PDF'
        WHEN content_type LIKE '%doc%' THEN 'DOC/DOCX'
        WHEN content_type LIKE '%image%' THEN 'Image'
        WHEN content_type LIKE '%text%' THEN 'Text'
        ELSE 'Other'
    END as type,
    COUNT(*) as count,
    COUNT(CASE WHEN text_file_path IS NULL THEN 1 END) as needs_text
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = $KRAUCK_ID
GROUP BY type;
EOF
echo ""

# Optional: Clear text_file_path to force re-extraction
echo "4. Reset text extraction flags?"
echo "   This will clear text_file_path to force re-extraction."
read -p "   Continue? (yes/no): " confirm

if [ "$confirm" = "yes" ]; then
    echo "   Clearing text_file_path for Krauck Systems attachments..."
    sqlite3 "$DB_PATH" <<EOF
UPDATE email_attachments 
SET text_file_path = NULL 
WHERE email_log_id IN (
    SELECT id FROM email_logs WHERE smtp_config_id = $KRAUCK_ID
);
EOF
    echo "   Done. Attachments will be re-processed on next email sync."
else
    echo "   Skipped reset."
fi

echo ""
echo "5. Manual re-processing script available at:"
echo "   /app/scripts/reprocess_attachments.py"
echo ""
echo "   To run manually:"
echo "   docker exec -it email-server python /app/scripts/reprocess_attachments.py"
echo ""
