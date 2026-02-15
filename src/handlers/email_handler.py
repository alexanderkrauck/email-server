"""FastAPI handlers for email server management."""

import json
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.database.connection import get_db
from src.email.email_processor import EmailProcessor
from src.email.smtp_sender import EmailSenderManager
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig

# Create global instances
email_sender_manager = EmailSenderManager()
email_processor = EmailProcessor()

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic models for API


class SMTPConfigCreate(BaseModel):
    name: str
    account_name: Optional[str] = None
    host: str
    port: int = 993
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    username: str
    password: str
    imap_use_ssl: bool = True
    imap_use_tls: bool = False
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = True
    enabled: bool = True

    # Storage overrides (NULL = use global setting)
    store_text_only_override: Optional[bool] = None
    max_attachment_size_override: Optional[int] = None
    extract_pdf_text_override: Optional[bool] = None
    extract_docx_text_override: Optional[bool] = None
    extract_image_text_override: Optional[bool] = None
    extract_other_text_override: Optional[bool] = None


class SMTPConfigUpdate(BaseModel):
    name: Optional[str] = None
    account_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    imap_use_ssl: Optional[bool] = None
    imap_use_tls: Optional[bool] = None
    smtp_use_ssl: Optional[bool] = None
    smtp_use_tls: Optional[bool] = None
    enabled: Optional[bool] = None

    # Storage overrides
    store_text_only_override: Optional[bool] = None
    max_attachment_size_override: Optional[int] = None
    extract_pdf_text_override: Optional[bool] = None
    extract_docx_text_override: Optional[bool] = None
    extract_image_text_override: Optional[bool] = None
    extract_other_text_override: Optional[bool] = None


class SMTPConfigResponse(BaseModel):
    id: int
    name: str
    account_name: Optional[str] = None
    host: str
    port: int
    smtp_host: Optional[str] = None
    smtp_port: int
    username: str
    imap_use_ssl: bool
    imap_use_tls: bool
    smtp_use_ssl: bool
    smtp_use_tls: bool
    enabled: bool
    created_at: str
    last_check: Optional[str] = None
    total_emails_processed: int

    # Storage override settings
    store_text_only_override: Optional[bool] = None
    max_attachment_size_override: Optional[int] = None
    extract_pdf_text_override: Optional[bool] = None
    extract_docx_text_override: Optional[bool] = None
    extract_image_text_override: Optional[bool] = None
    extract_other_text_override: Optional[bool] = None

    class Config:
        from_attributes = True


class AttachmentInfo(BaseModel):
    filename: str
    content_type: Optional[str] = None
    size: int = 0
    content: Optional[str] = None  # Extracted text content


class EmailResponse(BaseModel):
    id: int
    sender: str
    recipient: str
    subject: str
    email_date: str = ""
    processed_at: str
    content_size: int
    attachment_count: int
    attachments: List[AttachmentInfo] = []
    body_plain: str = ""
    body_html: str = ""

    class Config:
        from_attributes = True


class EmailSendRequest(BaseModel):
    smtp_config_id: int
    to_addresses: List[EmailStr]
    subject: str
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    cc_addresses: Optional[List[EmailStr]] = None
    bcc_addresses: Optional[List[EmailStr]] = None
    reply_to: Optional[EmailStr] = None


class EmailReplyRequest(BaseModel):
    smtp_config_id: int
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    cc_addresses: Optional[List[EmailStr]] = None
    include_original: bool = True


class EmailForwardRequest(BaseModel):
    smtp_config_id: int
    to_addresses: List[EmailStr]
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    cc_addresses: Optional[List[EmailStr]] = None
    bcc_addresses: Optional[List[EmailStr]] = None
    include_attachments: bool = True


class SearchResult(BaseModel):
    id: int
    sender: str
    recipient: str
    subject: str
    email_date: str = ""
    processed_at: str
    attachment_count: int
    attachments: List[AttachmentInfo] = []
    matched_field: str
    preview: str


# ─── Helper functions ───


def _generate_preview(text: str, query: str, length: int = 200) -> str:
    """Generate a preview snippet around the first regex match in text."""
    if not text or not query:
        return ""
    try:
        match = re.search(query, text, re.IGNORECASE)
        if match:
            start = max(0, match.start() - length // 2)
            end = min(len(text), match.end() + length // 2)
            preview = text[start:end].strip()
            if start > 0:
                preview = "..." + preview
            if end < len(text):
                preview = preview + "..."
            return preview
        return text[:length] + ("..." if len(text) > length else "")
    except re.error:
        return text[:length] + ("..." if len(text) > length else "")


def _build_attachment_infos(attachments: List[EmailAttachment], include_content: bool = False) -> List[AttachmentInfo]:
    """Build AttachmentInfo list from attachment models."""
    result = []
    for a in attachments:
        info = AttachmentInfo(
            filename=a.filename,
            content_type=a.content_type,
            size=a.size,
        )
        if include_content and a.text_content:
            info.content = a.text_content
        result.append(info)
    return result


# ─── SMTP Configuration endpoints ───


@router.get("/smtp-configs", response_model=List[SMTPConfigResponse])
async def list_smtp_configs(db: Session = Depends(get_db)):
    """
    List all SMTP/IMAP account configurations.

    ## Use for Filtering Searches
    Use smtp_config_id from this list to filter searches by account:

    ## Returns
    List of SMTP configs with:
    - id: Account ID (use for filtering emails)
    - name: Display name
    - account_name: Email address used for storage folder
    - host, port: IMAP server details
    - username: IMAP username
    - enabled: Whether account is active

    ## Example
    # First get account IDs:
    list_smtp_configs()

    # Then filter search by account:
    search_emails(query="invoice", smtp_config_id=1)
    """
    configs = db.query(SMTPConfig).all()
    return [config.dict() for config in configs]


@router.post("/smtp-configs", response_model=SMTPConfigResponse)
async def create_smtp_config(config_data: SMTPConfigCreate, db: Session = Depends(get_db)):
    """
    Create a new SMTP/IMAP account configuration.

    ## Storage Configuration

    The account can override global storage settings. Set to:
    - `true` to enable
    - `false` to explicitly disable
    - `null` (or omit) to use global default

    **Global stronger negative rule**: If global setting is disabled (false),
    account cannot enable it. If global is enabled, account can disable.

    ### Storage Override Fields

    - **store_text_only_override**: Store only text content, no binary
    - **max_attachment_size_override**: Max size for text extraction (bytes)
    - **extract_pdf_text_override**: Extract text from PDF attachments
    - **extract_docx_text_override**: Extract text from DOCX/DOC attachments
    - **extract_image_text_override**: Extract text from images via OCR
    - **extract_other_text_override**: Extract text from CSV, JSON, XML, RTF
    """
    existing = db.query(SMTPConfig).filter(SMTPConfig.name == config_data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="SMTP configuration with this name already exists")

    config = SMTPConfig(**config_data.dict())
    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info("Created SMTP config: %s", config.name)
    return config.dict()


@router.get("/smtp-configs/{config_id}", response_model=SMTPConfigResponse)
async def get_smtp_config(config_id: int, db: Session = Depends(get_db)):
    """Get a specific SMTP configuration."""
    config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="SMTP configuration not found")
    return config.dict()


@router.put("/smtp-configs/{config_id}", response_model=SMTPConfigResponse)
async def update_smtp_config(config_id: int, config_data: SMTPConfigUpdate, db: Session = Depends(get_db)):
    """Update an SMTP configuration."""
    config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="SMTP configuration not found")

    for field, value in config_data.dict(exclude_unset=True).items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)

    logger.info("Updated SMTP config: %s", config.name)
    return config.dict()


@router.delete("/smtp-configs/{config_id}")
async def delete_smtp_config(config_id: int, db: Session = Depends(get_db)):
    """Delete an SMTP configuration."""
    config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="SMTP configuration not found")

    name = config.name
    try:
        db.delete(config)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete '{name}': it has associated emails. Delete the emails first.",
        ) from None

    logger.info("Deleted SMTP config: %s", name)
    return {"message": f"SMTP configuration '{name}' deleted successfully"}


# ─── Email processing endpoints ───


@router.post("/smtp-configs/{config_id}/process")
async def process_server(config_id: int, db: Session = Depends(get_db)):
    """Manually trigger email processing for a specific server."""
    result = await email_processor.process_server_now(config_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/emails", response_model=List[EmailResponse])
async def list_emails(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """
    List all processed emails (most recent first).

    ## Parameters
    - **skip** (int, default=0): Pagination offset
    - **limit** (int, default=50): Max emails to return (max 100)

    ## Returns
    List of emails with metadata. Use get_email() to retrieve full content.
    """
    limit = min(limit, 100)
    emails = db.query(EmailLog).order_by(EmailLog.processed_at.desc()).offset(skip).limit(limit).all()

    result = []
    for email in emails:
        result.append(
            EmailResponse(
                id=email.id,
                sender=email.sender,
                recipient=email.recipient,
                subject=email.subject or "",
                email_date=email.email_date.isoformat() if email.email_date else "",
                processed_at=email.processed_at.isoformat(),
                content_size=len(email.body_plain or ""),
                attachment_count=email.attachment_count,
            )
        )
    return result


@router.get("/emails/search", response_model=List[SearchResult])
async def search_emails(
    query: str = "",
    search_attachments: bool = False,
    field: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    smtp_config_id: Optional[int] = None,
    has_attachments: bool = False,
    sort_by: str = "email_date",
    sort_order: str = "desc",
    participant: Optional[str] = None,
    from_me: bool = False,
    to_me: bool = False,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Search emails using regex patterns with Postgres.

    ## Two-Step Workflow for MCP Agents
    1. Call search_emails() to find matches (returns PREVIEWS only, ~200 chars)
    2. For each match, call get_email(id=EMAIL_ID, include_content=true)
       to retrieve FULL email content

    ## Parameters

    ### Required
    - **query** (str, default=""): Regex pattern. Examples:
      - "invoice" - simple word search
      - "invoice|receipt|bill" - OR pattern
      - "" (empty) - returns all emails matching filters

    ### Filtering
    - **search_attachments** (bool, default=False): Include attachment text in search
    - **field** (str, optional): Limit to specific field
      - "sender" - search in From address only
      - "subject" - search in Subject line only
      - "body" - search in email body only
      - "attachment" - search in attachment content only
    - **date_from** (str, optional): Filter emails after date (ISO format: "2024-01-15")
    - **date_to** (str, optional): Filter emails before date (ISO format: "2024-12-31")
    - **smtp_config_id** (int, optional): Filter by account ID
    - **has_attachments** (bool, default=False): Only return emails with attachments
    - **participant** (str, optional): Match sender OR recipient (partial match)
    - **from_me** (bool, default=False): Only emails sent from this account
    - **to_me** (bool, default=False): Only emails sent to this account

    ### Sorting
    - **sort_by** (str, default="email_date"): Sort field
    - **sort_order** (str, default="desc"): Sort direction

    ### Pagination
    - **skip** (int, default=0): Offset for pagination
    - **limit** (int, default=50): Max results to return (max 100)

    ## Returns
    List of SearchResult with id, sender, subject, matched_field, preview, etc.

    ## Next Step: Get Full Content
    get_email(id=42, include_content=True)
    """
    from datetime import datetime

    limit = min(limit, 100)

    # Get the account email for from_me/to_me filters
    account_email = None
    if smtp_config_id:
        config = db.query(SMTPConfig).filter(SMTPConfig.id == smtp_config_id).first()
        if config:
            account_email = config.username

    q = db.query(EmailLog)

    if smtp_config_id:
        q = q.filter(EmailLog.smtp_config_id == smtp_config_id)

    if has_attachments:
        q = q.filter(EmailLog.attachment_count > 0)

    if participant:
        q = q.filter((EmailLog.sender.ilike(f"%{participant}%")) | (EmailLog.recipient.ilike(f"%{participant}%")))

    if from_me and account_email:
        q = q.filter(EmailLog.sender.ilike(f"%{account_email}%"))

    if to_me and account_email:
        q = q.filter(EmailLog.recipient.ilike(f"%{account_email}%"))

    if date_from:
        try:
            from_date = datetime.fromisoformat(date_from)
            q = q.filter(EmailLog.email_date >= from_date)
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.fromisoformat(date_to)
            q = q.filter(EmailLog.email_date <= to_date)
        except ValueError:
            pass

    # Apply regex search via Postgres ~* (case-insensitive regex)
    if query:
        conditions = []
        needs_attachment_join = False

        if field == "sender":
            conditions.append(EmailLog.sender.op("~*")(query))
        elif field == "subject":
            conditions.append(EmailLog.subject.op("~*")(query))
        elif field == "body":
            conditions.append(EmailLog.body_plain.op("~*")(query))
        elif field == "attachment":
            needs_attachment_join = True
            conditions.append(EmailAttachment.text_content.op("~*")(query))
        else:
            # Search all standard fields
            conditions.append(EmailLog.sender.op("~*")(query))
            conditions.append(EmailLog.subject.op("~*")(query))
            conditions.append(EmailLog.body_plain.op("~*")(query))
            if search_attachments:
                needs_attachment_join = True
                conditions.append(EmailAttachment.text_content.op("~*")(query))

        if needs_attachment_join:
            q = q.outerjoin(EmailAttachment)

        q = q.filter(or_(*conditions))

        # Deduplicate when joining to attachments (one email can match multiple attachments)
        if needs_attachment_join:
            # Use subquery to get matching IDs, then query main table
            # (avoids Postgres DISTINCT ON / ORDER BY mismatch)
            matching_ids = q.with_entities(EmailLog.id).distinct().subquery()
            q = db.query(EmailLog).filter(EmailLog.id.in_(select(matching_ids)))

    # Sorting
    sort_column = EmailLog.email_date
    if sort_by == "processed_at":
        sort_column = EmailLog.processed_at
    elif sort_by == "sender":
        sort_column = EmailLog.sender
    elif sort_by == "subject":
        sort_column = EmailLog.subject

    if sort_order == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    email_logs = q.offset(skip).limit(limit).all()

    # Build results
    result = []
    for email in email_logs:
        attachments = db.query(EmailAttachment).filter(EmailAttachment.email_log_id == email.id).all()

        # Determine matched_field and preview
        matched_field = "metadata"
        preview = ""

        if query:
            # Check which field matched and generate preview
            body_text = email.body_plain or ""
            subject_text = email.subject or ""
            sender_text = email.sender or ""

            try:
                if field == "body" or (not field and re.search(query, body_text, re.IGNORECASE)):
                    matched_field = "body"
                    preview = _generate_preview(body_text, query)
                elif field == "subject" or (not field and re.search(query, subject_text, re.IGNORECASE)):
                    matched_field = "subject"
                    preview = _generate_preview(subject_text, query)
                elif field == "sender" or (not field and re.search(query, sender_text, re.IGNORECASE)):
                    matched_field = "sender"
                    preview = _generate_preview(sender_text, query)
                elif field == "attachment" or search_attachments:
                    for att in attachments:
                        att_text = att.text_content or ""
                        if re.search(query, att_text, re.IGNORECASE):
                            matched_field = "attachment"
                            preview = _generate_preview(att_text, query)
                            break
                else:
                    preview = _generate_preview(body_text, query)
            except re.error:
                # Invalid regex, fall through with empty preview
                preview = body_text[:200] if body_text else ""

        result.append(
            SearchResult(
                id=email.id,
                sender=email.sender,
                recipient=email.recipient,
                subject=email.subject or "",
                email_date=email.email_date.isoformat() if email.email_date else "",
                processed_at=email.processed_at.isoformat(),
                attachment_count=email.attachment_count,
                attachments=_build_attachment_infos(attachments),
                matched_field=matched_field,
                preview=preview,
            )
        )

    return result


@router.get("/emails/{email_id}", response_model=EmailResponse)
async def get_email(email_id: int, include_content: bool = True, db: Session = Depends(get_db)):
    """
    Get full email content by ID.

    ## Typical Workflow with Search
    1. First call search_emails() to find relevant emails
    2. Note the 'id' from search results
    3. Call this endpoint with that ID to get FULL content

    ## Parameters
    - **email_id** (int): The email ID from search results or list
    - **include_content** (bool, default=True):
      - True: Returns body_plain, body_html
      - False: Returns only metadata (sender, subject, etc.) - faster

    ## Returns
    - **id**: Email ID
    - **sender**: From address
    - **recipient**: To address
    - **subject**: Subject line
    - **body_plain**: Plain text content (if available)
    - **body_html**: HTML content (if available)
    - **attachments**: List of attachments with extracted text content
    - **attachment_count**: Number of attachments
    - **email_date**: Date from email headers
    - **processed_at**: When email was processed
    """
    email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    # Get attachments
    attachments = db.query(EmailAttachment).filter(EmailAttachment.email_log_id == email_id).all()

    response = EmailResponse(
        id=email.id,
        sender=email.sender,
        recipient=email.recipient,
        subject=email.subject or "",
        email_date=email.email_date.isoformat() if email.email_date else "",
        processed_at=email.processed_at.isoformat(),
        content_size=len(email.body_plain or ""),
        attachment_count=email.attachment_count,
        attachments=_build_attachment_infos(attachments, include_content=True),
    )

    if include_content:
        response.body_plain = email.body_plain or ""
        response.body_html = email.body_html or ""

    return response


# ─── System endpoints ───


@router.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """Get system status."""
    total_configs = db.query(SMTPConfig).count()
    enabled_configs = db.query(SMTPConfig).filter(SMTPConfig.enabled).count()
    total_emails = db.query(EmailLog).count()

    return {
        "status": "running",
        "processor_active": email_processor.processing,
        "smtp_configurations": {"total": total_configs, "enabled": enabled_configs},
        "emails_processed": total_emails,
    }


# ─── Email sending endpoints ───


@router.post("/send-email")
async def send_email(email_request: EmailSendRequest, db: Session = Depends(get_db)):
    """Send an email."""
    try:
        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=email_request.smtp_config_id,
            to_addresses=email_request.to_addresses,
            subject=email_request.subject,
            body_text=email_request.body_text,
            body_html=email_request.body_html,
            cc_addresses=email_request.cc_addresses,
            bcc_addresses=email_request.bcc_addresses,
            reply_to=email_request.reply_to,
        )

        if result["success"]:
            return {"message": "Email sent successfully", "details": result}
        raise HTTPException(status_code=400, detail=result["message"])

    except Exception as e:
        logger.error("Error sending email: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/send-email-with-attachments")
async def send_email_with_attachments(
    smtp_config_id: int,
    to_addresses: str,
    subject: str,
    body_text: Optional[str] = None,
    body_html: Optional[str] = None,
    cc_addresses: Optional[str] = None,
    bcc_addresses: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Send an email with file attachments."""
    try:
        to_list = json.loads(to_addresses)
        cc_list = json.loads(cc_addresses) if cc_addresses else None
        bcc_list = json.loads(bcc_addresses) if bcc_addresses else None

        attachment_data = []
        for upload_file in attachments:
            data = await upload_file.read()
            attachment_data.append({"data": data, "filename": upload_file.filename})

        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=smtp_config_id,
            to_addresses=to_list,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc_addresses=cc_list,
            bcc_addresses=bcc_list,
            reply_to=reply_to,
            attachments=attachment_data,
        )

        if result["success"]:
            return {"message": "Email with attachments sent successfully", "details": result}
        raise HTTPException(status_code=400, detail=result["message"])

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in address fields") from None
    except Exception as e:
        logger.error("Error sending email with attachments: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


# ─── Reply and Forward endpoints ───


@router.post("/emails/{email_id}/reply")
async def reply_to_email(email_id: int, reply_request: EmailReplyRequest, db: Session = Depends(get_db)):
    """Reply to a specific email."""
    try:
        original_email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
        if not original_email:
            raise HTTPException(status_code=404, detail="Original email not found")

        smtp_config = db.query(SMTPConfig).filter(SMTPConfig.id == reply_request.smtp_config_id).first()
        if not smtp_config:
            raise HTTPException(status_code=404, detail="SMTP configuration not found")

        # Build reply subject
        subject = original_email.subject or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        # Build reply body
        reply_body_text = reply_request.body_text or ""
        reply_body_html = reply_request.body_html or ""

        if reply_request.include_original:
            original_date = (
                original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"
            )
            original_intro = f"\n\nOn {original_date}, {original_email.sender} wrote:\n"
            original_plain = original_email.body_plain or ""

            if reply_body_text:
                quoted_original = "\n".join(f"> {line}" for line in original_plain.split("\n"))
                reply_body_text += original_intro + quoted_original

            if reply_body_html:
                reply_body_html += (
                    f"<br><br>On {original_date}, {original_email.sender} wrote:"
                    f"<br><blockquote>{original_plain}</blockquote>"
                )

        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=reply_request.smtp_config_id,
            to_addresses=[original_email.sender],
            subject=subject,
            body_text=reply_body_text,
            body_html=reply_body_html,
            cc_addresses=reply_request.cc_addresses,
            reply_to=smtp_config.username,
            in_reply_to=original_email.message_id,
            references=original_email.message_id,
        )

        if result["success"]:
            return {"message": "Reply sent successfully", "details": result}
        raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error sending reply: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/emails/{email_id}/forward")
async def forward_email(email_id: int, forward_request: EmailForwardRequest, db: Session = Depends(get_db)):
    """Forward a specific email."""
    try:
        original_email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
        if not original_email:
            raise HTTPException(status_code=404, detail="Original email not found")

        smtp_config = db.query(SMTPConfig).filter(SMTPConfig.id == forward_request.smtp_config_id).first()
        if not smtp_config:
            raise HTTPException(status_code=404, detail="SMTP configuration not found")

        # Build forward subject
        subject = original_email.subject or ""
        if not subject.lower().startswith("fwd:"):
            subject = f"Fwd: {subject}"

        # Build forward body with original message
        forward_body_text = forward_request.body_text or ""
        forward_body_html = forward_request.body_html or ""

        original_date = (
            original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"
        )
        original_plain = original_email.body_plain or ""

        original_header = "\n\n---------- Forwarded message ----------\n"
        original_header += f"From: {original_email.sender}\n"
        original_header += f"Date: {original_date}\n"
        original_header += f"Subject: {original_email.subject or '(no subject)'}\n"
        original_header += f"To: {original_email.recipient}\n\n"

        forward_body_text += original_header + original_plain

        if forward_body_html:
            html_header = "<br><br>---------- Forwarded message ----------<br>"
            html_header += f"<b>From:</b> {original_email.sender}<br>"
            html_header += f"<b>Date:</b> {original_date}<br>"
            html_header += f"<b>Subject:</b> {original_email.subject or '(no subject)'}<br>"
            html_header += f"<b>To:</b> {original_email.recipient}<br><br>"
            forward_body_html += html_header + original_plain

        # Handle attachments if requested
        attachment_data = []
        if forward_request.include_attachments:
            attachments = db.query(EmailAttachment).filter(EmailAttachment.email_log_id == email_id).all()
            for attachment in attachments:
                if attachment.text_content:
                    attachment_data.append(
                        {"data": attachment.text_content.encode("utf-8"), "filename": attachment.filename}
                    )

        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=forward_request.smtp_config_id,
            to_addresses=forward_request.to_addresses,
            subject=subject,
            body_text=forward_body_text,
            body_html=forward_body_html,
            cc_addresses=forward_request.cc_addresses,
            bcc_addresses=forward_request.bcc_addresses,
            reply_to=smtp_config.username,
            attachments=attachment_data if attachment_data else None,
        )

        if result["success"]:
            return {"message": "Email forwarded successfully", "details": result}
        raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error forwarding email: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/smtp-configs/{config_id}/test-connection")
async def test_smtp_connection(config_id: int, db: Session = Depends(get_db)):
    """Test SMTP connection for sending."""
    config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="SMTP configuration not found")

    if not config.enabled:
        raise HTTPException(status_code=400, detail="SMTP configuration is disabled")

    try:
        sender = await email_sender_manager.get_sender(config)
        success = await sender.connect()
        sender.disconnect()

        if success:
            return {"message": f"Successfully connected to {config.name}", "status": "connected"}
        return {"message": f"Failed to connect to {config.name}", "status": "failed"}

    except Exception as e:
        logger.error("Error testing SMTP connection: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
