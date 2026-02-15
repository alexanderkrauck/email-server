"""FastAPI handlers for email server management."""

import base64
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import List, Optional

from src.database.connection import get_db
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig
from src.email.attachment_handler import AttachmentHandler
from src.email.email_logger import EmailLogger
from src.email.email_processor import EmailProcessor
from src.email.markdown_converter import EmailToMarkdownConverter
from src.email.smtp_sender import EmailSenderManager
from src.email.search_service import SearchService
from src.config import settings

# Create global instance
email_sender_manager = EmailSenderManager()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Global processor instance
email_processor = EmailProcessor()


# Pydantic models for API
class SMTPConfigCreate(BaseModel):
    name: str
    account_name: str = None
    host: str
    port: int = 993
    smtp_host: str = None
    smtp_port: int = 587
    username: str
    password: str
    imap_use_ssl: bool = True
    imap_use_tls: bool = False
    smtp_use_ssl: bool = False
    smtp_use_tls: bool = True
    enabled: bool = True


class SMTPConfigUpdate(BaseModel):
    name: str = None
    account_name: str = None
    host: str = None
    port: int = None
    smtp_host: str = None
    smtp_port: int = None
    username: str = None
    password: str = None
    imap_use_ssl: bool = None
    imap_use_tls: bool = None
    smtp_use_ssl: bool = None
    smtp_use_tls: bool = None
    enabled: bool = None


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

    class Config:
        from_attributes = True


class AttachmentInfo(BaseModel):
    filename: str
    content_type: str = None
    size: int
    content: Optional[str] = None  # Base64 encoded content for small files


class EmailResponse(BaseModel):
    id: int
    sender: str
    recipient: str
    subject: str
    email_date: str = None
    processed_at: str
    content_size: int
    attachment_count: int
    attachments: List[AttachmentInfo] = []
    body_plain: str = None
    body_html: str = None
    markdown_content: str = None
    file_path: str = None

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
    email_date: Optional[str] = None
    processed_at: str
    attachment_count: int
    matched_field: str
    preview: str
    file_path: str


# SMTP Configuration endpoints

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
    """Create a new SMTP configuration."""
    # Check if name already exists
    existing = db.query(SMTPConfig).filter(SMTPConfig.name == config_data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="SMTP configuration with this name already exists")

    config = SMTPConfig(**config_data.dict())
    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info(f"Created SMTP config: {config.name}")
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

    # Update fields
    for field, value in config_data.dict(exclude_unset=True).items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)

    logger.info(f"Updated SMTP config: {config.name}")
    return config.dict()


@router.delete("/smtp-configs/{config_id}")
async def delete_smtp_config(config_id: int, db: Session = Depends(get_db)):
    """Delete an SMTP configuration."""
    config = db.query(SMTPConfig).filter(SMTPConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="SMTP configuration not found")

    name = config.name
    db.delete(config)
    db.commit()

    logger.info(f"Deleted SMTP config: {name}")
    return {"message": f"SMTP configuration '{name}' deleted successfully"}


# Email processing endpoints
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
    
    ## Example
    # Get first 50 emails
    list_emails(skip=0, limit=50)
    
    # Get next 50
    list_emails(skip=50, limit=50)
    """
    emails = db.query(EmailLog).order_by(EmailLog.processed_at.desc()).offset(skip).limit(limit).all()

    result = []
    for email in emails:
        email_response = EmailResponse(
            id=email.id,
            sender=email.sender,
            recipient=email.recipient,
            subject=email.subject,
            email_date=email.email_date.isoformat() if email.email_date else None,
            processed_at=email.processed_at.isoformat(),
            content_size=0,
            attachment_count=email.attachment_count,
            file_path=email.log_file_path
        )
        result.append(email_response)

    return result


@router.get("/emails/search", response_model=List[SearchResult])
async def search_emails(
    query: str,
    search_attachments: bool = False,
    field: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    smtp_config_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Search emails using regex patterns on filesystem text files.
    
    ## Two-Step Workflow for MCP Agents
    1. Call search_emails() to find matches (returns PREVIEWS only, ~200 chars)
    2. For each match, call get_email(id=EMAIL_ID, include_content=true) 
       to retrieve FULL email content
    
    ## Parameters
    
    ### Required
    - **query** (str): Regex pattern. Examples:
      - "invoice" - simple word search
      - "invoice|receipt|bill" - OR pattern  
      - "^From:.*@company\\.com" - regex (escape dots with double backslash)
    
    ### Filtering
    - **search_attachments** (bool, default=False): Include attachment text in search
      - Set to true to search inside PDF/DOCX/XLSX extracted text
      - Set to false (default) to search ONLY email body/subject
    - **field** (str, optional): Limit to specific field
      - "sender" - search in From address only
      - "subject" - search in Subject line only
      - "body" - search in email body only
      - "attachment" - search in attachment content only
    - **date_from** (str, optional): Filter emails after date (ISO format: "2024-01-15")
    - **date_to** (str, optional): Filter emails before date (ISO format: "2024-12-31")
    - **smtp_config_id** (int, optional): Filter by account ID (see GET /api/v1/smtp-configs)
    
    ### Pagination
    - **skip** (int, default=0): Offset for pagination
    - **limit** (int, default=50): Max results to return
    
    ## Common Use Cases (Verified Example Calls)
    
    ### Search all emails containing "invoice"
    search_emails(query="invoice", limit=10)
    
    ### Search attachments for "contract", last 30 days
    search_emails(query="contract", search_attachments=True, date_from="2024-01-15", limit=20)
    
    ### Search only from specific sender domain
    search_emails(query="@company\\.com", field="sender", limit=10)
    
    ### Search subject lines only for "meeting"
    search_emails(query="meeting", field="subject", limit=10)
    
    ### Search specific account, exclude attachments, date range
    search_emails(
        query="report", 
        smtp_config_id=1,
        date_from="2024-01-01",
        date_to="2024-06-30",
        search_attachments=False,
        limit=25
    )
    
    ### Get emails with attachments only (via date filter + search pattern ".")
    search_emails(query=".", date_from="2024-01-01", limit=50)
    # Note: attachment_count in results indicates attachments exist
    
    ### Exclude attachments from search (faster)
    search_emails(query="invoice", search_attachments=False, limit=10)
    
    ## Returns
    
    List of SearchResult with:
    - **id**: Email ID (use for get_email)
    - **sender**: From address
    - **subject**: Subject line
    - **matched_field**: Where match was found ("body"|"attachment"|"metadata")
    - **preview**: ~200 char context around match
    - **file_path**: Path to full text file on disk
    - **email_date**: Date from email headers
    - **processed_at**: When email was processed
    - **attachment_count**: Number of attachments
    
    ## Next Step: Get Full Content
    
    After finding relevant matches with search, get FULL content:
    
    # Get full content for email ID 42
    get_email(id=42, include_content=True)
    
    This returns body_plain, body_html (if available), attachment list with metadata.
    """
    from datetime import datetime
    
    q = db.query(EmailLog)
    
    if smtp_config_id:
        q = q.filter(EmailLog.smtp_config_id == smtp_config_id)
    
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
    
    email_logs = q.all()
    email_ids = [e.id for e in email_logs]
    
    search_service = SearchService()
    
    fields = None
    if field:
        if field == "sender":
            fields = ["metadata"]
        elif field == "subject":
            fields = ["metadata"]
        elif field == "body":
            fields = ["body"]
        elif field == "attachment":
            fields = ["attachment"]
    
    matches = await search_service.search(
        query=query,
        email_ids=email_ids if email_ids else None,
        fields=fields,
        include_attachments=search_attachments,
        limit=limit
    )
    
    result = []
    for match in matches:
        email = next((e for e in email_logs if e.id == match.email_id), None)
        if email:
            result.append(SearchResult(
                id=email.id,
                sender=email.sender,
                recipient=email.recipient,
                subject=email.subject or "",
                email_date=email.email_date.isoformat() if email.email_date else None,
                processed_at=email.processed_at.isoformat(),
                attachment_count=email.attachment_count,
                matched_field=match.matched_field,
                preview=match.preview,
                file_path=match.file_path
            ))
    
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
      - True: Returns body_plain, body_html, markdown_content
      - False: Returns only metadata (sender, subject, etc.) - faster
    
    ## Returns
    - **id**: Email ID
    - **sender**: From address
    - **recipient**: To address  
    - **subject**: Subject line
    - **body_plain**: Plain text content (if available)
    - **body_html**: HTML content (if available)
    - **file_path**: Path to text file on disk
    - **attachments**: List of attachments with:
      - filename: Original filename
      - content_type: MIME type (e.g., "application/pdf")
      - size: Size in bytes
      - content: Base64-encoded content (only for small files <100KB)
    - **attachment_count**: Number of attachments
    - **email_date**: Date from email headers
    - **processed_at**: When email was processed
    
    ## Example
    # After searching and finding email ID 42 with interesting preview:
    get_email(id=42, include_content=True)
    
    # Get only metadata (faster, no body content):
    get_email(id=42, include_content=False)
    
    ## File Paths
    The file_path points to .txt files in /app/data/emails/{account}/emails/{year-month}/
    These contain the full plain text content of each email.
    """
    email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    # Get attachments
    attachments = db.query(EmailAttachment).filter(EmailAttachment.email_log_id == email_id).all()
    attachment_infos = []

    if attachments:
        attachment_handler = AttachmentHandler()
        for attachment in attachments:
            attachment_info = AttachmentInfo(
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=attachment.size
            )

            # For small attachments, include base64 content
            if attachment.size <= 1024 * 100:  # 100KB limit for inline content
                try:
                    data = await attachment_handler.get_attachment_data(attachment)
                    if data:
                        attachment_info.content = base64.b64encode(data).decode('utf-8')
                except Exception as e:
                    logger.warning(f"Could not load attachment {attachment.id}: {e}")

            attachment_infos.append(attachment_info)

    # Prepare response
    response = EmailResponse(
        id=email.id,
        sender=email.sender,
        recipient=email.recipient,
        subject=email.subject,
        email_date=email.email_date.isoformat() if email.email_date else None,
        processed_at=email.processed_at.isoformat(),
        content_size=0,
        attachment_count=email.attachment_count,
        attachments=attachment_infos,
        file_path=email.log_file_path
    )

    # Include content if requested
    if include_content and email.log_file_path:
        try:
            from pathlib import Path
            text_path = Path(email.log_file_path)
            if text_path.exists():
                response.body_plain = text_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not read email content: {e}")

    return response


# Old content endpoint removed - content now included in main email endpoint


# System endpoints
@router.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """Get system status."""
    total_configs = db.query(SMTPConfig).count()
    enabled_configs = db.query(SMTPConfig).filter(SMTPConfig.enabled).count()
    total_emails = db.query(EmailLog).count()

    # Get email logger instance
    email_logger = EmailLogger()
    log_files = email_logger.get_log_files(limit=10)

    return {
        "status": "running",
        "processor_active": email_processor.processing,
        "smtp_configurations": {
            "total": total_configs,
            "enabled": enabled_configs
        },
        "emails_processed": total_emails,
        "recent_log_files": len(log_files)
    }


@router.get("/log-files")
async def list_log_files(limit: int = 50):
    """List recent log files."""
    email_logger = EmailLogger()
    return {"files": email_logger.get_log_files(limit=limit)}


@router.post("/cleanup-logs")
async def cleanup_logs(days_old: int = 30):
    """Clean up old log files."""
    email_logger = EmailLogger()
    deleted_count = await email_logger.cleanup_old_logs(days_old)
    return {"message": f"Cleaned up {deleted_count} log files older than {days_old} days"}


# Old attachment endpoints removed - attachments now included in main email endpoint


# Email sending endpoints
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
            reply_to=email_request.reply_to
        )

        if result["success"]:
            return {"message": "Email sent successfully", "details": result}
        else:
            raise HTTPException(status_code=400, detail=result["message"])

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send-email-with-attachments")
async def send_email_with_attachments(
    smtp_config_id: int,
    to_addresses: str,  # JSON string of email addresses
    subject: str,
    body_text: Optional[str] = None,
    body_html: Optional[str] = None,
    cc_addresses: Optional[str] = None,
    bcc_addresses: Optional[str] = None,
    reply_to: Optional[str] = None,
    attachments: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    """Send an email with file attachments."""
    try:
        # Parse JSON string parameters
        to_list = json.loads(to_addresses)
        cc_list = json.loads(cc_addresses) if cc_addresses else None
        bcc_list = json.loads(bcc_addresses) if bcc_addresses else None

        # Process attachments
        attachment_data = []
        for upload_file in attachments:
            data = await upload_file.read()
            attachment_data.append({
                'data': data,
                'filename': upload_file.filename
            })

        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=smtp_config_id,
            to_addresses=to_list,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc_addresses=cc_list,
            bcc_addresses=bcc_list,
            reply_to=reply_to,
            attachments=attachment_data
        )

        if result["success"]:
            return {"message": "Email with attachments sent successfully", "details": result}
        else:
            raise HTTPException(status_code=400, detail=result["message"])

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in address fields")
    except Exception as e:
        logger.error(f"Error sending email with attachments: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Reply and Forward endpoints
@router.post("/emails/{email_id}/reply")
async def reply_to_email(email_id: int, reply_request: EmailReplyRequest, db: Session = Depends(get_db)):
    """Reply to a specific email."""
    try:
        # Get original email
        original_email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
        if not original_email:
            raise HTTPException(status_code=404, detail="Original email not found")

        # Get SMTP config for sending
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
            # Format original content - read from filesystem
            original_date = original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"
            original_intro = f"\n\nOn {original_date}, {original_email.sender} wrote:\n"
            
            original_plain = ""
            original_html = ""
            
            if original_email.log_file_path:
                try:
                    text_path = Path(original_email.log_file_path)
                    if text_path.exists():
                        original_plain = text_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Could not read original email content: {e}")

            if reply_body_text:
                quoted_original = '\n'.join(f"> {line}" for line in original_plain.split('\n'))
                reply_body_text += original_intro + quoted_original

            if reply_body_html:
                reply_body_html += f"<br><br>On {original_date}, {original_email.sender} wrote:<br><blockquote>{original_plain}</blockquote>"

        # Send reply
        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=reply_request.smtp_config_id,
            to_addresses=[original_email.sender],
            subject=subject,
            body_text=reply_body_text,
            body_html=reply_body_html,
            cc_addresses=reply_request.cc_addresses,
            reply_to=smtp_config.username,
            in_reply_to=original_email.message_id,
            references=original_email.message_id
        )

        if result["success"]:
            return {"message": "Reply sent successfully", "details": result}
        else:
            raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending reply: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emails/{email_id}/forward")
async def forward_email(email_id: int, forward_request: EmailForwardRequest, db: Session = Depends(get_db)):
    """Forward a specific email."""
    try:
        # Get original email
        original_email = db.query(EmailLog).filter(EmailLog.id == email_id).first()
        if not original_email:
            raise HTTPException(status_code=404, detail="Original email not found")

        # Get SMTP config for sending
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

        # Format original message - read from filesystem
        original_date = original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"
        
        original_plain = ""
        if original_email.log_file_path:
            try:
                text_path = Path(original_email.log_file_path)
                if text_path.exists():
                    original_plain = text_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not read original email content: {e}")

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
            if attachments:
                attachment_handler = AttachmentHandler()
                for attachment in attachments:
                    try:
                        data = await attachment_handler.get_attachment_data(attachment)
                        if data:
                            attachment_data.append({
                                'data': data,
                                'filename': attachment.filename
                            })
                    except Exception as e:
                        logger.warning(f"Could not forward attachment {attachment.id}: {e}")

        # Send forward
        result = await email_sender_manager.send_email_via_config(
            smtp_config_id=forward_request.smtp_config_id,
            to_addresses=forward_request.to_addresses,
            subject=subject,
            body_text=forward_body_text,
            body_html=forward_body_html,
            cc_addresses=forward_request.cc_addresses,
            bcc_addresses=forward_request.bcc_addresses,
            reply_to=smtp_config.username,
            attachments=attachment_data if attachment_data else None
        )

        if result["success"]:
            return {"message": "Email forwarded successfully", "details": result}
        else:
            raise HTTPException(status_code=400, detail=result["message"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error forwarding email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Template sending removed - use simple send-email endpoint instead


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
        else:
            return {"message": f"Failed to connect to {config.name}", "status": "failed"}

    except Exception as e:
        logger.error(f"Error testing SMTP connection: {e}")
        raise HTTPException(status_code=500, detail=str(e))