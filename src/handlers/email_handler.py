"""FastAPI handlers for email server management."""

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from src.database.connection import get_db
from src.models.smtp_config import SMTPConfig
from src.models.email import EmailLog
from src.models.attachment import EmailAttachment
from src.email.email_processor import EmailProcessor
from src.email.email_logger import EmailLogger
from src.email.attachment_handler import AttachmentHandler
from src.email.smtp_sender import EmailSenderManager

# Create global instance
email_sender_manager = EmailSenderManager()
import logging
import io

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


# SMTP Configuration endpoints
@router.get("/smtp-configs", response_model=List[SMTPConfigResponse])
async def list_smtp_configs(db: Session = Depends(get_db)):
    """List all SMTP configurations."""
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
    """List processed emails with basic info."""
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
            content_size=email.content_size,
            attachment_count=email.attachment_count,
            file_path=email.log_file_path
        )
        result.append(email_response)

    return result


@router.get("/emails/{email_id}", response_model=EmailResponse)
async def get_email(email_id: int, include_content: bool = True, db: Session = Depends(get_db)):
    """Get a specific email with full content and attachments."""
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
                    import base64
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
        content_size=email.content_size,
        attachment_count=email.attachment_count,
        attachments=attachment_infos,
        file_path=email.log_file_path
    )

    # Include content if requested
    if include_content:
        response.body_plain = email.body_plain
        response.body_html = email.body_html

        # Generate markdown content on-the-fly
        from src.email.markdown_converter import EmailToMarkdownConverter
        converter = EmailToMarkdownConverter()
        email_data = {
            'sender': email.sender,
            'recipient': email.recipient,
            'subject': email.subject,
            'email_date': email.email_date,
            'message_id': email.message_id,
            'content_size': email.content_size,
            'attachment_count': email.attachment_count,
            'body_plain': email.body_plain,
            'body_html': email.body_html,
            'attachments': attachments
        }
        response.markdown_content = converter.convert_email_to_markdown(email_data)

    return response


# Old content endpoint removed - content now included in main email endpoint


# System endpoints
@router.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """Get system status."""
    total_configs = db.query(SMTPConfig).count()
    enabled_configs = db.query(SMTPConfig).filter(SMTPConfig.enabled == True).count()
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
        import json

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
            # Format original content
            original_date = original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"
            original_intro = f"\n\nOn {original_date}, {original_email.sender} wrote:\n"

            if reply_body_text:
                original_plain = original_email.body_plain or ""
                quoted_original = '\n'.join(f"> {line}" for line in original_plain.split('\n'))
                reply_body_text += original_intro + quoted_original

            if reply_body_html:
                original_html = original_email.body_html or original_email.body_plain or ""
                reply_body_html += f"<br><br>On {original_date}, {original_email.sender} wrote:<br><blockquote>{original_html}</blockquote>"

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

        # Format original message
        original_date = original_email.email_date.strftime("%Y-%m-%d %H:%M:%S") if original_email.email_date else "Unknown"

        original_header = f"\n\n---------- Forwarded message ----------\n"
        original_header += f"From: {original_email.sender}\n"
        original_header += f"Date: {original_date}\n"
        original_header += f"Subject: {original_email.subject or '(no subject)'}\n"
        original_header += f"To: {original_email.recipient}\n\n"

        forward_body_text += original_header + (original_email.body_plain or "")

        if forward_body_html:
            html_header = f"<br><br>---------- Forwarded message ----------<br>"
            html_header += f"<b>From:</b> {original_email.sender}<br>"
            html_header += f"<b>Date:</b> {original_date}<br>"
            html_header += f"<b>Subject:</b> {original_email.subject or '(no subject)'}<br>"
            html_header += f"<b>To:</b> {original_email.recipient}<br><br>"

            forward_body_html += html_header + (original_email.body_html or original_email.body_plain or "")

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