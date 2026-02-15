#!/bin/bash
# Factory Reset Script for Email Server
# Wipes all data for all accounts while preserving account registration
#
# Usage:
#   ./factory_reset.sh --dry-run    # Show what would be deleted
#   ./factory_reset.sh --force      # Actually delete everything

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -f "/app/data/emailserver.db" ]]; then
    DB_PATH="${DB_PATH:-/app/data/emailserver.db}"
    DATA_DIR="${DATA_DIR:-/app/data}"
else
    DB_PATH="${DB_PATH:-$PROJECT_DIR/data/emailserver.db}"
    DATA_DIR="${DATA_DIR:-$PROJECT_DIR/data}"
fi
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
DRY_RUN=false
FORCE=false
BUILD=false
CONTAINER_WAS_RUNNING=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Factory Reset for Email Server"
    echo ""
    echo "Wipes all email data, attachments, and sync state while preserving"
    echo "account registrations (SMTP/IMAP configs)."
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --dry-run       Show what would be deleted without making changes"
    echo "  --force         Actually perform the reset (required for destructive ops)"
    echo "  --build         Rebuild Docker image before restarting container"
    echo "  --db PATH       Path to SQLite database (default: /app/data/emailserver.db)"
    echo "  --data-dir DIR  Path to data directory (default: /app/data)"
    echo "  -h, --help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run                              # Preview what would be deleted"
    echo "  $0 --force                                # Perform reset"
    echo "  $0 --force --build                        # Reset and rebuild Docker image"
    echo "  $0 --dry-run --db ./data/emailserver.db   # Use custom DB path"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --build)
            BUILD=true
            shift
            ;;
        --db)
            DB_PATH="$2"
            shift 2
            ;;
        --data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate arguments
if [[ "$DRY_RUN" == false && "$FORCE" == false ]]; then
    echo -e "${RED}Error: Must specify either --dry-run or --force${NC}"
    echo ""
    echo "Use --dry-run to see what would be deleted"
    echo "Use --force to actually perform the reset"
    exit 1
fi

# Check if DB exists
if [[ ! -f "$DB_PATH" ]]; then
    echo -e "${RED}Error: Database not found at $DB_PATH${NC}"
    exit 1
fi

echo -e "${GREEN}=== Email Server Factory Reset ===${NC}"
echo ""
echo "Database: $DB_PATH"
echo "Data directory: $DATA_DIR"
echo "Mode: $([[ "$DRY_RUN" == true ]] && echo "DRY RUN (preview only)" || echo "LIVE (destructive)")"
echo ""

# Get account count before
ACCOUNT_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM smtp_configs;" 2>/dev/null || echo "0")
echo "Registered accounts (will be preserved): $ACCOUNT_COUNT"

# Get data counts before
EMAIL_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM email_logs;" 2>/dev/null || echo "0")
ATTACHMENT_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM email_attachments;" 2>/dev/null || echo "0")

echo "Emails to delete: $EMAIL_COUNT"
echo "Attachments to delete: $ATTACHMENT_COUNT"
echo ""

# Show account details
if [[ $ACCOUNT_COUNT -gt 0 ]]; then
    echo "Accounts that will be preserved:"
    sqlite3 "$DB_PATH" "SELECT id, name, account_name, host FROM smtp_configs;" | while read line; do
        echo "  - $line"
    done
    echo ""
fi

# Calculate storage size
if [[ -d "$DATA_DIR" ]]; then
    DATA_SIZE=$(du -sh "$DATA_DIR" 2>/dev/null | cut -f1)
    echo "Current data directory size: $DATA_SIZE"
    
    # Check for email subdirectories
    if [[ -d "$DATA_DIR/emails" ]]; then
        EMAILS_SIZE=$(du -sh "$DATA_DIR/emails" 2>/dev/null | cut -f1)
        echo "  - emails/: $EMAILS_SIZE"
    fi
fi
echo ""

# Check if container is running
is_container_running() {
    docker ps --filter "name=email-server" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "email-server"
}

# Dry run: just show what would happen
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}=== DRY RUN - Would perform the following: ===${NC}"
    echo ""
    if is_container_running; then
        echo "CONTAINER:"
        echo "  1. Stop email-server container (currently running)"
        echo ""
    fi
    echo "DATABASE OPERATIONS:"
    echo "  2. DELETE FROM email_attachments (all $ATTACHMENT_COUNT records)"
    echo "  3. DELETE FROM email_logs (all $EMAIL_COUNT records)"
    echo "  4. VACUUM database to reclaim space"
    echo ""
    echo "STORAGE OPERATIONS:"
    echo "  5. Remove all files under $DATA_DIR/emails/"
    echo "  6. Remove temp files from /tmp/email_attachments/"
    echo ""
    if [[ "$BUILD" == true ]]; then
        echo "CONTAINER:"
        echo "  7. Rebuild and start email-server container"
        echo ""
    elif is_container_running; then
        echo "CONTAINER:"
        echo "  7. Restart email-server container"
        echo ""
    fi
    echo "PRESERVED (NOT DELETED):"
    echo "  - smtp_configs table (all $ACCOUNT_COUNT accounts)"
    echo "  - Database file itself"
    echo ""
    echo -e "${GREEN}Run with --force to actually perform the reset.${NC}"
    exit 0
fi

echo "Proceeding with factory reset..."
echo ""

# Step 0: Stop container if running
if is_container_running; then
    CONTAINER_WAS_RUNNING=true
    echo "[0/6] Stopping email-server container..."
    docker stop email-server
    echo "      Done."
else
    echo "[0/6] Container not running, skipping stop."
fi

# Step 1: Delete attachment records
echo "[1/6] Deleting attachment records..."
sqlite3 "$DB_PATH" "DELETE FROM email_attachments;"
echo "      Done."

# Step 2: Delete email logs
echo "[2/6] Deleting email logs..."
sqlite3 "$DB_PATH" "DELETE FROM email_logs;"
echo "      Done."

# Step 3: Vacuum database
echo "[3/6] Vacuuming database..."
sqlite3 "$DB_PATH" "VACUUM;"
echo "      Done."

# Step 4: Clean up storage
echo "[4/6] Cleaning up storage directories..."

# Remove all email subdirectories but keep structure
if [[ -d "$DATA_DIR/emails" ]]; then
    # Find all account directories and remove their contents
    find "$DATA_DIR/emails" -mindepth 1 -maxdepth 1 -type d | while read account_dir; do
        echo "      Cleaning $(basename "$account_dir")..."
        rm -rf "$account_dir"/*
    done
    echo "      Email directories cleaned."
fi

# Remove temp files
if [[ -d "/tmp/email_attachments" ]]; then
    rm -rf /tmp/email_attachments/*
    echo "      Temp files cleaned."
fi

echo "      Done."

# Step 5: Verify
echo "[5/6] Verifying reset..."
NEW_EMAIL_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM email_logs;")
NEW_ATTACHMENT_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM email_attachments;")
NEW_ACCOUNT_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM smtp_configs;")

echo ""
echo -e "${GREEN}=== Reset Complete ===${NC}"
echo ""
echo "Remaining data:"
echo "  Accounts: $NEW_ACCOUNT_COUNT (preserved)"
echo "  Emails: $NEW_EMAIL_COUNT"
echo "  Attachments: $NEW_ATTACHMENT_COUNT"
echo ""

if [[ $NEW_EMAIL_COUNT -eq 0 && $NEW_ATTACHMENT_COUNT -eq 0 && $NEW_ACCOUNT_COUNT -eq $ACCOUNT_COUNT ]]; then
    # Step 6: Rebuild and/or restart container
    if [[ "$BUILD" == true ]]; then
        echo "[6/6] Rebuilding and starting email-server container..."
        docker rm -f email-server 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" up -d --build
        echo "      Done."
        echo ""
    elif [[ "$CONTAINER_WAS_RUNNING" == true ]]; then
        echo "[6/6] Starting email-server container..."
        docker start email-server
        echo "      Done."
        echo ""
    fi
    echo -e "${GREEN}Factory reset successful! Sync will start fresh for all accounts.${NC}"
    exit 0
else
    echo -e "${YELLOW}Warning: Reset verification mismatch${NC}"
    exit 1
fi
