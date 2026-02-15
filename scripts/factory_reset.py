#!/usr/bin/env python3
"""
Factory Reset Tool for Email Server

Wipes all email data while preserving account configurations.

Usage:
    python factory_reset.py --dry-run
    python factory_reset.py --force
    python factory_reset.py --force --db /path/to/emailserver.db
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple


class Colors:
    """Terminal colors."""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


class FactoryReset:
    """Factory reset tool for email server."""
    
    # Tables to PRESERVE (account registration)
    KEEP_TABLES = ['smtp_configs']
    
    # Tables to PURGE (all email data)
    PURGE_TABLES = ['email_attachments', 'email_logs']
    
    def __init__(self, db_path: str, data_dir: str, dry_run: bool = True):
        self.db_path = db_path
        self.data_dir = Path(data_dir)
        self.dry_run = dry_run
        self.conn = None
    
    def connect(self) -> bool:
        """Connect to database."""
        if not os.path.exists(self.db_path):
            print(f"{Colors.RED}Error: Database not found at {self.db_path}{Colors.NC}")
            return False
        
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            return True
        except sqlite3.Error as e:
            print(f"{Colors.RED}Error connecting to database: {e}{Colors.NC}")
            return False
    
    def get_stats(self) -> dict:
        """Get current database stats."""
        stats = {}
        cursor = self.conn.cursor()
        
        # Account count
        cursor.execute("SELECT COUNT(*) FROM smtp_configs")
        stats['accounts'] = cursor.fetchone()[0]
        
        # Email count
        cursor.execute("SELECT COUNT(*) FROM email_logs")
        stats['emails'] = cursor.fetchone()[0]
        
        # Attachment count
        cursor.execute("SELECT COUNT(*) FROM email_attachments")
        stats['attachments'] = cursor.fetchone()[0]
        
        # Account details
        cursor.execute("SELECT id, name, account_name, host FROM smtp_configs")
        stats['account_details'] = cursor.fetchall()
        
        return stats
    
    def get_accounts(self) -> List[sqlite3.Row]:
        """Get all accounts."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM smtp_configs")
        return cursor.fetchall()
    
    def get_storage_size(self) -> str:
        """Get total storage size."""
        try:
            total = 0
            if self.data_dir.exists():
                for dirpath, dirnames, filenames in os.walk(self.data_dir):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        if os.path.exists(fp):
                            total += os.path.getsize(fp)
            
            # Convert to human readable
            for unit in ['B', 'KB', 'MB', 'GB']:
                if total < 1024:
                    return f"{total:.1f} {unit}"
                total /= 1024
            return f"{total:.1f} TB"
        except Exception as e:
            return f"unknown ({e})"
    
    def preview(self):
        """Show preview of what would be deleted."""
        stats = self.get_stats()
        
        print(f"{Colors.GREEN}=== Factory Reset Preview ==={Colors.NC}")
        print()
        print(f"Database: {self.db_path}")
        print(f"Data directory: {self.data_dir}")
        print(f"Mode: {Colors.YELLOW}DRY RUN{Colors.NC}")
        print()
        print(f"Accounts to preserve: {stats['accounts']}")
        print(f"Emails to delete: {stats['emails']}")
        print(f"Attachments to delete: {stats['attachments']}")
        print()
        
        if stats['account_details']:
            print("Accounts that will be preserved:")
            for row in stats['account_details']:
                print(f"  - {row['id']}: {row['name']} ({row['account_name']}) @ {row['host']}")
            print()
        
        print(f"Storage size: {self.get_storage_size()}")
        print()
        print(f"{Colors.YELLOW}Operations that would be performed:{Colors.NC}")
        print("  Database:")
        for table in self.PURGE_TABLES:
            print(f"    - DELETE FROM {table}")
        print("    - VACUUM")
        print()
        print("  Storage:")
        print(f"    - Remove all files under {self.data_dir}/emails/")
        print("    - Clean temp files from /tmp/email_attachments/")
        print()
        print(f"{Colors.GREEN}Run with --force to execute.{Colors.NC}")
    
    def reset(self) -> bool:
        """Perform factory reset."""
        stats = self.get_stats()
        
        print(f"{Colors.RED}=== FACTORY RESET ==={Colors.NC}")
        print()
        print("This will PERMANENTLY DELETE:")
        print(f"  - {stats['emails']} emails")
        print(f"  - {stats['attachments']} attachment records")
        print(f"  - All email content files")
        print()
        print("The following will be PRESERVED:")
        print(f"  - {stats['accounts']} account configurations")
        print()
        
        # Execute database operations
        print("[1/4] Clearing database tables...")
        cursor = self.conn.cursor()
        
        for table in self.PURGE_TABLES:
            try:
                cursor.execute(f"DELETE FROM {table}")
                print(f"      Deleted {cursor.rowcount} rows from {table}")
            except sqlite3.Error as e:
                print(f"{Colors.RED}      Error deleting from {table}: {e}{Colors.NC}")
        
        self.conn.commit()
        print("      Done.")
        
        print("[2/4] Vacuuming database...")
        cursor.execute("VACUUM")
        print("      Done.")
        
        print("[3/4] Cleaning storage directories...")
        emails_dir = self.data_dir / "emails"
        if emails_dir.exists():
            for account_dir in emails_dir.iterdir():
                if account_dir.is_dir():
                    print(f"      Cleaning {account_dir.name}...")
                    for item in account_dir.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        elif item.is_file():
                            item.unlink()
        
        # Clean temp files
        temp_dir = Path("/tmp/email_attachments")
        if temp_dir.exists():
            for item in temp_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
        
        print("      Done.")
        
        print("[4/4] Verifying reset...")
        new_stats = self.get_stats()
        print(f"      Accounts preserved: {new_stats['accounts']}")
        print(f"      Emails remaining: {new_stats['emails']}")
        print(f"      Attachments remaining: {new_stats['attachments']}")
        
        if (new_stats['emails'] == 0 and 
            new_stats['attachments'] == 0 and 
            new_stats['accounts'] == stats['accounts']):
            print()
            print(f"{Colors.GREEN}=== Factory reset completed successfully! ==={Colors.NC}")
            return True
        else:
            print()
            print(f"{Colors.RED}=== Reset verification failed! ==={Colors.NC}")
            return False
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Factory reset for email server - wipes all data except accounts'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would be deleted')
    parser.add_argument('--force', action='store_true',
                        help='Actually perform the reset (destructive)')
    parser.add_argument('--db', default='/app/data/emailserver.db',
                        help='Path to SQLite database')
    parser.add_argument('--data-dir', default='/app/data',
                        help='Path to data directory')
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.force:
        print("Error: Must specify either --dry-run or --force")
        print("Use --dry-run to preview, --force to execute")
        return 1
    
    reset = FactoryReset(
        db_path=args.db,
        data_dir=args.data_dir,
        dry_run=not args.force
    )
    
    if not reset.connect():
        return 1
    
    try:
        if args.dry_run:
            reset.preview()
            return 0
        else:
            # Extra confirmation
            print(f"{Colors.RED}WARNING: This will permanently delete all email data!{Colors.NC}")
            confirm = input("Type 'RESET' to confirm: ")
            if confirm != 'RESET':
                print("Aborted.")
                return 1
            
            success = reset.reset()
            return 0 if success else 1
    finally:
        reset.close()


if __name__ == '__main__':
    sys.exit(main())
