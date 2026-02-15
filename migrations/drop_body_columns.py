#!/usr/bin/env python3
"""Migration script to drop deprecated body columns from email_logs table."""

import sqlite3
import sys
from pathlib import Path

def migrate():
    db_path = Path(__file__).parent.parent / "data" / "emailserver.db"
    
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        sys.exit(1)
    
    # Create backup before migration
    backup_path = db_path.with_suffix(f".db.backup.pre-migration-{Path(__file__).stem}")
    import shutil
    shutil.copy2(db_path, backup_path)
    print(f"Created backup: {backup_path}")
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Check if columns exist
    cursor.execute("PRAGMA table_info(email_logs)")
    columns = {col[1] for col in cursor.fetchall()}
    
    # Drop body_plain if exists
    if "body_plain" in columns:
        print("Dropping column: body_plain")
        cursor.execute("ALTER TABLE email_logs DROP COLUMN body_plain")
    else:
        print("Column body_plain does not exist, skipping")
    
    # Drop body_html if exists
    if "body_html" in columns:
        print("Dropping column: body_html")
        cursor.execute("ALTER TABLE email_logs DROP COLUMN body_html")
    else:
        print("Column body_html does not exist, skipping")
    
    conn.commit()
    conn.close()
    
    print("Migration completed successfully!")

if __name__ == "__main__":
    migrate()
