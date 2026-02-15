#!/usr/bin/env python3
"""
Reset and reprocess attachments for Krauck Systems.

This script handles the case where:
- Old attachments were processed before the binary-save fix
- Binary files don't exist for those attachments
- We need to either mark them as orphaned or re-fetch from IMAP

Usage:
    python reset_attachments.py [--dry-run] [--mark-orphaned] [--check-files]
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.smtp_config import SMTPConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = "/app/data/emailserver.db"
DATA_DIR = "/app/data/emails"


def get_db_session():
    """Create database session."""
    engine = create_engine(f"sqlite:///{DB_PATH}")
    Session = sessionmaker(bind=engine)
    return Session()


def get_krauck_config(session):
    """Get Krauck Systems SMTP config."""
    return session.query(SMTPConfig).filter(
        SMTPConfig.name.ilike("%krauck%") | SMTPConfig.account_name.ilike("%krauck%")
    ).first()


def check_attachment_files(session, config_id):
    """Check which attachments have files on disk."""
    attachments = session.query(EmailAttachment).join(EmailLog).filter(
        EmailLog.smtp_config_id == config_id
    ).all()
    
    results = {
        'total': len(attachments),
        'has_binary': 0,
        'missing_binary': 0,
        'has_text': 0,
        'missing_text': 0,
        'orphaned': []  # DB record exists but no file
    }
    
    for att in attachments:
        # Check binary file
        if att.file_path and Path(att.file_path).exists():
            results['has_binary'] += 1
        else:
            results['missing_binary'] += 1
            results['orphaned'].append(att)
        
        # Check text file
        if att.text_file_path and Path(att.text_file_path).exists():
            results['has_text'] += 1
        else:
            results['missing_text'] += 1
    
    return results


def diagnose(session, config):
    """Run diagnostics on attachment state."""
    logger.info("=== Attachment Diagnostics for Krauck Systems ===")
    logger.info("")
    
    # Get stats
    total_emails = session.query(EmailLog).filter_by(smtp_config_id=config.id).count()
    total_attachments = session.query(EmailAttachment).join(EmailLog).filter(
        EmailLog.smtp_config_id == config.id
    ).count()
    
    logger.info(f"Config: {config.name} (ID: {config.id})")
    logger.info(f"Account: {config.account_name}")
    logger.info(f"Total emails: {total_emails}")
    logger.info(f"Total attachments in DB: {total_attachments}")
    logger.info("")
    
    # Check file existence
    logger.info("Checking file existence...")
    results = check_attachment_files(session, config.id)
    
    logger.info(f"  Attachments with binary file: {results['has_binary']}")
    logger.info(f"  Attachments missing binary: {results['missing_binary']}")
    logger.info(f"  Attachments with text: {results['has_text']}")
    logger.info(f"  Attachments missing text: {results['missing_text']}")
    logger.info("")
    
    # Content type breakdown
    logger.info("Content types needing processing:")
    content_stats = session.query(EmailAttachment).join(EmailLog).filter(
        EmailLog.smtp_config_id == config.id
    ).all()
    
    types = {}
    for att in content_stats:
        group = 'other'
        if att.content_type:
            if 'pdf' in att.content_type.lower():
                group = 'PDF'
            elif 'doc' in att.content_type.lower():
                group = 'DOC/DOCX'
            elif 'image' in att.content_type.lower():
                group = 'Image'
            elif 'text' in att.content_type.lower():
                group = 'Text'
        
        if group not in types:
            types[group] = {'total': 0, 'missing_binary': 0, 'missing_text': 0}
        
        types[group]['total'] += 1
        if not att.file_path or not Path(att.file_path).exists():
            types[group]['missing_binary'] += 1
        if not att.text_file_path or not Path(att.text_file_path).exists():
            types[group]['missing_text'] += 1
    
    for group, stats in sorted(types.items(), key=lambda x: -x[1]['total']):
        logger.info(f"  {group}: {stats['total']} total, "
                   f"{stats['missing_binary']} missing binary, "
                   f"{stats['missing_text']} missing text")
    
    logger.info("")
    
    # Show orphaned attachments
    if results['orphaned']:
        logger.info(f"Orphaned attachments (no binary file): {len(results['orphaned'])}")
        for att in results['orphaned'][:5]:
            logger.info(f"  - ID {att.id}: {att.filename} ({att.content_type})")
        if len(results['orphaned']) > 5:
            logger.info(f"  ... and {len(results['orphaned']) - 5} more")
    
    return results


def reset_text_extraction(session, config, dry_run=True):
    """Reset text extraction flags to force reprocessing."""
    attachments = session.query(EmailAttachment).join(EmailLog).filter(
        EmailLog.smtp_config_id == config.id,
        EmailAttachment.file_path.isnot(None)  # Only if we have the binary
    ).all()
    
    # Filter to only those with existing files
    to_reset = [att for att in attachments if Path(att.file_path).exists()]
    
    logger.info(f"Attachments that can be reprocessed: {len(to_reset)}")
    
    if dry_run:
        logger.info("DRY RUN - would reset these attachments:")
        for att in to_reset[:5]:
            logger.info(f"  - ID {att.id}: {att.filename}")
        if len(to_reset) > 5:
            logger.info(f"  ... and {len(to_reset) - 5} more")
        return
    
    # Actually reset
    count = 0
    for att in to_reset:
        att.text_file_path = None
        count += 1
    
    session.commit()
    logger.info(f"Reset text_file_path for {count} attachments")


def mark_orphaned(session, config, dry_run=True):
    """Mark attachments without binary files as orphaned."""
    attachments = session.query(EmailAttachment).join(EmailLog).filter(
        EmailLog.smtp_config_id == config.id
    ).all()
    
    orphaned = [att for att in attachments 
                if not att.file_path or not Path(att.file_path).exists()]
    
    logger.info(f"Orphaned attachments (no binary): {len(orphaned)}")
    
    if dry_run:
        logger.info("DRY RUN - would mark as orphaned:")
        for att in orphaned[:5]:
            logger.info(f"  - ID {att.id}: {att.filename}")
        if len(orphaned) > 5:
            logger.info(f"  ... and {len(orphaned) - 5} more")
        return
    
    # Option 1: Delete orphaned records
    # Option 2: Set a flag (would need schema change)
    # For now, just log them
    
    logger.info("Orphaned attachments identified but not deleted:")
    logger.info("These attachments were processed before the binary-save fix.")
    logger.info("They cannot be reprocessed without re-fetching from IMAP.")


def main():
    parser = argparse.ArgumentParser(description='Reset attachment pipeline for Krauck Systems')
    parser.add_argument('--diagnose', action='store_true', help='Run diagnostics only')
    parser.add_argument('--check-files', action='store_true', help='Check file existence')
    parser.add_argument('--reset-text', action='store_true', help='Reset text extraction flags')
    parser.add_argument('--mark-orphaned', action='store_true', help='Mark orphaned attachments')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    
    args = parser.parse_args()
    
    # Default to diagnose if no action specified
    if not any([args.diagnose, args.check_files, args.reset_text, args.mark_orphaned]):
        args.diagnose = True
    
    session = get_db_session()
    
    try:
        config = get_krauck_config(session)
        if not config:
            logger.error("Krauck Systems config not found!")
            configs = session.query(SMTPConfig).all()
            logger.info("Available configs:")
            for c in configs:
                logger.info(f"  - {c.id}: {c.name} ({c.account_name})")
            return 1
        
        if args.diagnose:
            diagnose(session, config)
        
        if args.check_files:
            logger.info("\n=== File Check ===")
            results = check_attachment_files(session, config.id)
            logger.info(f"Binary files present: {results['has_binary']}")
            logger.info(f"Binary files missing: {results['missing_binary']}")
        
        if args.reset_text:
            logger.info("\n=== Reset Text Extraction ===")
            reset_text_extraction(session, config, dry_run=args.dry_run)
        
        if args.mark_orphaned:
            logger.info("\n=== Mark Orphaned ===")
            mark_orphaned(session, config, dry_run=args.dry_run)
        
    finally:
        session.close()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
