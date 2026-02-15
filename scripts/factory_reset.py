#!/usr/bin/env python3
"""
Factory Reset Tool for Email Server (PostgreSQL)

Wipes all email data while preserving account configurations.

Usage:
    python factory_reset.py --dry-run
    python factory_reset.py --force
    python factory_reset.py --force --db-url postgresql://user:pass@host:5432/db
"""

import argparse
import sys

import psycopg2
from psycopg2.extras import RealDictCursor


class Colors:
    """Terminal colors."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    NC = "\033[0m"  # No Color


DEFAULT_DB_URL = "postgresql://emailserver:emailserver@postgres:5432/emailserver"

# Tables to PRESERVE (account registration)
KEEP_TABLES = ["smtp_configs"]

# Tables to PURGE (all email data) - order matters for foreign keys
PURGE_TABLES = ["email_attachments", "email_logs", "email_status"]


class FactoryReset:
    """Factory reset tool for email server."""

    def __init__(self, db_url: str, dry_run: bool = True):
        self.db_url = db_url
        self.dry_run = dry_run
        self.conn = None

    def connect(self) -> bool:
        """Connect to database."""
        try:
            self.conn = psycopg2.connect(self.db_url)
            self.conn.autocommit = False
            return True
        except psycopg2.Error as e:
            print(f"{Colors.RED}Error connecting to database: {e}{Colors.NC}")
            return False

    def get_stats(self) -> dict:
        """Get current database stats."""
        stats = {}
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as count FROM smtp_configs")
            stats["accounts"] = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) as count FROM email_logs")
            stats["emails"] = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) as count FROM email_attachments")
            stats["attachments"] = cur.fetchone()["count"]

            cur.execute("SELECT id, name, account_name, host FROM smtp_configs")
            stats["account_details"] = cur.fetchall()

        return stats

    def get_db_size(self) -> str:
        """Get total database size."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                return cur.fetchone()[0]
        except Exception as e:
            return f"unknown ({e})"

    def preview(self):
        """Show preview of what would be deleted."""
        stats = self.get_stats()

        print(f"{Colors.GREEN}=== Factory Reset Preview ==={Colors.NC}")
        print()
        print(f"Database: {self.db_url.split('@')[-1] if '@' in self.db_url else self.db_url}")
        print(f"Mode: {Colors.YELLOW}DRY RUN{Colors.NC}")
        print()
        print(f"Accounts to preserve: {stats['accounts']}")
        print(f"Emails to delete: {stats['emails']}")
        print(f"Attachments to delete: {stats['attachments']}")
        print()

        if stats["account_details"]:
            print("Accounts that will be preserved:")
            for row in stats["account_details"]:
                print(f"  - {row['id']}: {row['name']} ({row['account_name']}) @ {row['host']}")
            print()

        print(f"Database size: {self.get_db_size()}")
        print()
        print(f"{Colors.YELLOW}Operations that would be performed:{Colors.NC}")
        print("  Database:")
        for table in PURGE_TABLES:
            print(f"    - TRUNCATE {table} CASCADE")
        print("    - VACUUM FULL")
        print()
        print(f"{Colors.GREEN}Run with --force to execute.{Colors.NC}")

    def reset(self) -> bool:
        """Perform factory reset."""
        stats = self.get_stats()

        print(f"{Colors.RED}=== FACTORY RESET ==={Colors.NC}")
        print()
        print("This will PERMANENTLY DELETE:")
        print(f"  - {stats['emails']} emails (including body content)")
        print(f"  - {stats['attachments']} attachment records (including extracted text)")
        print()
        print("The following will be PRESERVED:")
        print(f"  - {stats['accounts']} account configurations")
        print()

        # Execute database operations
        print("[1/3] Clearing database tables...")

        with self.conn.cursor() as cur:
            for table in PURGE_TABLES:
                try:
                    cur.execute(f"TRUNCATE TABLE {table} CASCADE")
                    print(f"      Truncated {table}")
                except psycopg2.Error as e:
                    print(f"{Colors.RED}      Error truncating {table}: {e}{Colors.NC}")
                    self.conn.rollback()
                    return False

        self.conn.commit()
        print("      Done.")

        print("[2/3] Vacuuming database...")
        old_autocommit = self.conn.autocommit
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute("VACUUM FULL")
        self.conn.autocommit = old_autocommit
        print("      Done.")

        print("[3/3] Verifying reset...")
        new_stats = self.get_stats()
        print(f"      Accounts preserved: {new_stats['accounts']}")
        print(f"      Emails remaining: {new_stats['emails']}")
        print(f"      Attachments remaining: {new_stats['attachments']}")

        if new_stats["emails"] == 0 and new_stats["attachments"] == 0 and new_stats["accounts"] == stats["accounts"]:
            print()
            print(f"{Colors.GREEN}=== Factory reset completed successfully! ==={Colors.NC}")
            return True
        print()
        print(f"{Colors.RED}=== Reset verification failed! ==={Colors.NC}")
        return False

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


def main():
    parser = argparse.ArgumentParser(description="Factory reset for email server - wipes all data except accounts")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be deleted")
    parser.add_argument("--force", action="store_true", help="Actually perform the reset (destructive)")
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")

    args = parser.parse_args()

    if not args.dry_run and not args.force:
        print("Error: Must specify either --dry-run or --force")
        print("Use --dry-run to preview, --force to execute")
        return 1

    reset = FactoryReset(db_url=args.db_url, dry_run=not args.force)

    if not reset.connect():
        return 1

    try:
        if args.dry_run:
            reset.preview()
            return 0
        # Extra confirmation
        print(f"{Colors.RED}WARNING: This will permanently delete all email data!{Colors.NC}")
        confirm = input("Type 'RESET' to confirm: ")
        if confirm != "RESET":
            print("Aborted.")
            return 1

        success = reset.reset()
        return 0 if success else 1
    finally:
        reset.close()


if __name__ == "__main__":
    sys.exit(main())
