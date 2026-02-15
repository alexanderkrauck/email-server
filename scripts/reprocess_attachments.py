#!/usr/bin/env python3
"""
Reprocess attachments for Krauck Systems.
Extracts text from saved attachment files and updates the database.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.base import Base
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig
from src.email.text_extractor import TextExtractor
from src.storage_config.resolver import resolve_storage_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database path
DB_PATH = "/app/data/emailserver.db"
DATA_DIR = "/app/data/emails"


def get_db_session():
    """Create database session."""
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    return Session()


def get_krauck_config_id(session):
    """Get Krauck Systems SMTP config ID."""
    config = session.query(SMTPConfig).filter(
        SMTPConfig.name.ilike("%krauck%") | SMTPConfig.account_name.ilike("%krauck%")
    ).first()
    
    if not config:
        # Try exact match
        config = session.query(SMTPConfig).filter_by(name="Krauck Systems").first()
    
    return config.id if config else None


async def reprocess_attachment(session, attachment: EmailAttachment, storage_config):
    """Reprocess a single attachment to extract text."""
    try:
        # Check if we have the binary file
        if not attachment.file_path or not Path(attachment.file_path).exists():
            logger.warning(f"  No binary file for attachment {attachment.id}: {attachment.filename}")
            return False
        
        # Read binary content
        with open(attachment.file_path, 'rb') as f:
            payload = f.read()
        
        if not payload:
            logger.warning(f"  Empty file for attachment {attachment.id}")
            return False
        
        # Extract text
        text_extractor = TextExtractor()
        text_content = await text_extractor.extract(payload, attachment.content_type, storage_config)
        
        if not text_content:
            logger.debug(f"  No text extracted for {attachment.filename} ({attachment.content_type})")
            return False
        
        # Save extracted text
        from src.email.attachment_handler import AttachmentHandler
        handler = AttachmentHandler()
        
        # Get account name from email log
        email_log = session.query(EmailLog).get(attachment.email_log_id)
        account_name = None
        if email_log and email_log.smtp_config_id:
            config = session.query(SMTPConfig).get(email_log.smtp_config_id)
            if config:
                account_name = config.account_name or config.name
        
        text_file_path = await handler._save_attachment_text(
            text_content, attachment.filename, attachment.email_log_id, account_name
        )
        
        if text_file_path:
            attachment.text_file_path = str(text_file_path)
            session.commit()
            logger.info(f"  ✓ Extracted text for {attachment.filename} -> {text_file_path}")
            return True
        else:
            logger.error(f"  Failed to save text for {attachment.filename}")
            return False
            
    except Exception as e:
        logger.error(f"  Error processing attachment {attachment.id}: {e}")
        return False


async def main():
    """Main reprocessing function."""
    logger.info("=== Attachment Reprocessing for Krauck Systems ===")
    
    session = get_db_session()
    
    try:
        # Get Krauck Systems config
        krauck_id = get_krauck_config_id(session)
        if not krauck_id:
            logger.error("Krauck Systems config not found!")
            # Show available configs
            configs = session.query(SMTPConfig).all()
            logger.info("Available configs:")
            for c in configs:
                logger.info(f"  - {c.id}: {c.name} ({c.account_name})")
            return 1
        
        logger.info(f"Found Krauck Systems config ID: {krauck_id}")
        
        # Get storage config
        storage_config = resolve_storage_config(None)
        
        # Get attachments that need processing
        attachments = session.query(EmailAttachment).join(EmailLog).filter(
            EmailLog.smtp_config_id == krauck_id
        ).all()
        
        logger.info(f"Found {len(attachments)} total attachments")
        
        # Count stats
        needs_processing = [a for a in attachments if not a.text_file_path]
        has_text = [a for a in attachments if a.text_file_path]
        missing_file = [a for a in attachments if not a.file_path]
        
        logger.info(f"  - With text extracted: {len(has_text)}")
        logger.info(f"  - Need text extraction: {len(needs_processing)}")
        logger.info(f"  - Missing binary file: {len(missing_file)}")
        
        if not needs_processing:
            logger.info("No attachments need processing. Exiting.")
            return 0
        
        # Process attachments
        logger.info(f"\nProcessing {len(needs_processing)} attachments...")
        
        success_count = 0
        fail_count = 0
        
        for i, attachment in enumerate(needs_processing, 1):
            logger.info(f"[{i}/{len(needs_processing)}] Processing {attachment.filename}...")
            
            if await reprocess_attachment(session, attachment, storage_config):
                success_count += 1
            else:
                fail_count += 1
        
        logger.info(f"\n=== Results ===")
        logger.info(f"Successfully processed: {success_count}")
        logger.info(f"Failed: {fail_count}")
        
        # Final stats
        final_stats = session.query(EmailAttachment).join(EmailLog).filter(
            EmailLog.smtp_config_id == krauck_id,
            EmailAttachment.text_file_path.isnot(None)
        ).count()
        
        logger.info(f"Total attachments with text: {final_stats}/{len(attachments)}")
        
    finally:
        session.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
