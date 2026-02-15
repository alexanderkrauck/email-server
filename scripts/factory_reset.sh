#!/bin/bash
# Factory Reset Script for Email Server (PostgreSQL)
# Wipes all data for all accounts while preserving account registration
#
# Usage:
#   ./factory_reset.sh --dry-run    # Show what would be deleted
#   ./factory_reset.sh --force      # Actually delete everything

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# Default connection - works inside docker network
DB_URL="${EMAILSERVER_DATABASE_URL:-postgresql://emailserver:emailserver@localhost:5432/emailserver}"
DRY_RUN=false
FORCE=false
BUILD=false
CONTAINER_WAS_RUNNING=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Helper to run psql commands
run_psql() {
    psql "$DB_URL" -t -A -c "$1" 2>/dev/null
}

usage() {
    echo "Factory Reset for Email Server (PostgreSQL)"
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
    echo "  --db-url URL    PostgreSQL connection URL (default: from EMAILSERVER_DATABASE_URL)"
    echo "  -h, --help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --dry-run"
    echo "  $0 --force"
    echo "  $0 --force --build"
    echo "  $0 --dry-run --db-url postgresql://user:pass@localhost:5432/emailserver"
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
        --db-url)
            DB_URL="$2"
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

# Check psql is available
if ! command -v psql &> /dev/null; then
    echo -e "${RED}Error: psql not found. Install postgresql-client.${NC}"
    exit 1
fi

# Test connection
if ! run_psql "SELECT 1" > /dev/null 2>&1; then
    echo -e "${RED}Error: Cannot connect to database at $DB_URL${NC}"
    exit 1
fi

echo -e "${GREEN}=== Email Server Factory Reset ===${NC}"
echo ""
echo "Database: $(echo "$DB_URL" | sed 's/:[^@]*@/@/')"
echo "Mode: $([[ "$DRY_RUN" == true ]] && echo "DRY RUN (preview only)" || echo "LIVE (destructive)")"
echo ""

# Get data counts
ACCOUNT_COUNT=$(run_psql "SELECT COUNT(*) FROM smtp_configs;")
EMAIL_COUNT=$(run_psql "SELECT COUNT(*) FROM email_logs;")
ATTACHMENT_COUNT=$(run_psql "SELECT COUNT(*) FROM email_attachments;")

echo "Registered accounts (will be preserved): $ACCOUNT_COUNT"
echo "Emails to delete: $EMAIL_COUNT"
echo "Attachments to delete: $ATTACHMENT_COUNT"
echo ""

# Show account details
if [[ $ACCOUNT_COUNT -gt 0 ]]; then
    echo "Accounts that will be preserved:"
    run_psql "SELECT id || ': ' || name || ' (' || account_name || ') @ ' || host FROM smtp_configs;" | while read line; do
        echo "  - $line"
    done
    echo ""
fi

# Database size
DB_SIZE=$(run_psql "SELECT pg_size_pretty(pg_database_size(current_database()));")
echo "Current database size: $DB_SIZE"
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
    echo "  2. TRUNCATE email_attachments CASCADE ($ATTACHMENT_COUNT records)"
    echo "  3. TRUNCATE email_logs CASCADE ($EMAIL_COUNT records)"
    echo "  4. VACUUM FULL to reclaim space"
    echo ""
    if [[ "$BUILD" == true ]]; then
        echo "CONTAINER:"
        echo "  6. Rebuild and start email-server container"
        echo ""
    elif is_container_running; then
        echo "CONTAINER:"
        echo "  6. Restart email-server container"
        echo ""
    fi
    echo "PRESERVED (NOT DELETED):"
    echo "  - smtp_configs table (all $ACCOUNT_COUNT accounts)"
    echo ""
    echo -e "${GREEN}Run with --force to actually perform the reset.${NC}"
    exit 0
fi

echo "Proceeding with factory reset..."
echo ""

# Step 0: Stop container if running
if is_container_running; then
    CONTAINER_WAS_RUNNING=true
    echo "[0/5] Stopping email-server container..."
    docker stop email-server
    echo "      Done."
else
    echo "[0/5] Container not running, skipping stop."
fi

# Step 1: Truncate tables
echo "[1/5] Clearing database tables..."
run_psql "TRUNCATE TABLE email_attachments CASCADE;"
echo "      Truncated email_attachments."
run_psql "TRUNCATE TABLE email_logs CASCADE;"
echo "      Truncated email_logs."
echo "      Done."

# Step 2: Vacuum
echo "[2/5] Vacuuming database..."
run_psql "VACUUM FULL;"
echo "      Done."

# Step 3: Verify
echo "[3/5] Verifying reset..."
NEW_EMAIL_COUNT=$(run_psql "SELECT COUNT(*) FROM email_logs;")
NEW_ATTACHMENT_COUNT=$(run_psql "SELECT COUNT(*) FROM email_attachments;")
NEW_ACCOUNT_COUNT=$(run_psql "SELECT COUNT(*) FROM smtp_configs;")

echo ""
echo -e "${GREEN}=== Reset Complete ===${NC}"
echo ""
echo "Remaining data:"
echo "  Accounts: $NEW_ACCOUNT_COUNT (preserved)"
echo "  Emails: $NEW_EMAIL_COUNT"
echo "  Attachments: $NEW_ATTACHMENT_COUNT"
echo ""

if [[ $NEW_EMAIL_COUNT -eq 0 && $NEW_ATTACHMENT_COUNT -eq 0 && $NEW_ACCOUNT_COUNT -eq $ACCOUNT_COUNT ]]; then
    # Step 4: Rebuild and/or restart container
    if [[ "$BUILD" == true ]]; then
        echo "[4/5] Rebuilding and starting email-server container..."
        docker rm -f email-server 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" up -d --build
        echo "      Done."
        echo ""
    elif [[ "$CONTAINER_WAS_RUNNING" == true ]]; then
        echo "[4/5] Starting email-server container..."
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
