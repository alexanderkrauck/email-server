# Attachment Pipeline

## Stored Data

The database stores:

- attachment ID and provider part reference
- original filename
- claimed and detected MIME type
- size and SHA-256
- extraction state, error, and extractor version
- bounded extracted text

Original binary payloads are not persisted.

## Ingestion

The MIME payload exists in memory while a newly synced RFC822 message is parsed. Oversized attachments skip extraction. Supported payloads run in a bounded process pool with memory, time, page, and output limits.

Legacy Office formats are not passed to OOXML readers. PDF pages with no text do not abort the whole extraction.

## Retrieval

`get_attachment` returns metadata, bounded extracted text, and an expiring signed URL. The URL is bound to the owner and attachment ID.

When requested, the service:

1. verifies the signature and expiry;
2. verifies tenant ownership;
3. reconnects to the owning provider;
4. locates the message by persisted folder/UID or legacy RFC `Message-ID`;
5. selects the MIME part by provider part reference, content ID, or filename;
6. verifies or records its SHA-256;
7. streams the original bytes to the caller.

The bytes are released after the response and are not written to object storage.

## Forwarding

Forwarding uses the same refetch path, so an original PDF remains a PDF. Extracted text is never sent under the original binary filename.
