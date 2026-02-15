-- Attachment Pipeline Diagnostic for Krauck Systems
-- Run this in the SQLite database to check attachment status

-- 1. Get Krauck Systems config ID
SELECT id, name, account_name, host, enabled
FROM smtp_configs 
WHERE name LIKE '%Krauck%' OR account_name LIKE '%Krauck%';

-- 2. Overall attachment stats for Krauck Systems
SELECT 
    COUNT(*) as total_attachments,
    COUNT(CASE WHEN file_path IS NOT NULL THEN 1 END) as with_binary_file,
    COUNT(CASE WHEN text_file_path IS NOT NULL THEN 1 END) as with_extracted_text,
    COUNT(CASE WHEN file_path IS NULL THEN 1 END) as missing_binary,
    COUNT(CASE WHEN file_path IS NOT NULL AND text_file_path IS NULL THEN 1 END) as needs_text_extraction
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1);

-- 3. Attachments by content type needing extraction
SELECT 
    CASE 
        WHEN content_type LIKE '%pdf%' THEN 'PDF'
        WHEN content_type LIKE '%doc%' THEN 'DOC/DOCX'
        WHEN content_type LIKE '%image%' THEN 'Image'
        WHEN content_type LIKE '%text%' THEN 'Text'
        ELSE content_type
    END as content_type_group,
    COUNT(*) as count,
    COUNT(CASE WHEN text_file_path IS NULL THEN 1 END) as needs_extraction
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1)
GROUP BY content_type_group
ORDER BY count DESC;

-- 4. List specific attachments missing text extraction
SELECT 
    ea.id,
    ea.filename,
    ea.content_type,
    ea.size,
    ea.file_path,
    ea.text_file_path,
    el.id as email_id,
    el.sender,
    el.subject
FROM email_attachments ea
JOIN email_logs el ON ea.email_log_id = el.id
WHERE el.smtp_config_id = (SELECT id FROM smtp_configs WHERE name LIKE '%Krauck%' LIMIT 1)
    AND ea.file_path IS NOT NULL
    AND ea.text_file_path IS NULL
ORDER BY ea.id DESC
LIMIT 20;

-- 5. Check if binary files actually exist on disk
-- (This needs to be run separately with a script)
