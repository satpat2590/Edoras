#!/bin/bash
# Daily SQLite database backup
# Retains 7 daily backups in ~/edoras/backups/
set -e

export PATH="/home/satyamini/miniconda3/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="$SCRIPT_DIR/../crypto_data.db"
BACKUP_DIR="$SCRIPT_DIR/../backups"

mkdir -p "$BACKUP_DIR"

DATESTAMP=$(date -u +"%Y-%m-%d")
BACKUP_FILE="$BACKUP_DIR/crypto_data_${DATESTAMP}.db"

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting backup..."

# Use SQLite's .backup command for a safe hot backup (works while DB is in use)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Backup complete: $BACKUP_FILE ($SIZE)"

# Prune backups older than 7 days
find "$BACKUP_DIR" -name "crypto_data_*.db" -mtime +7 -delete
REMAINING=$(ls "$BACKUP_DIR"/crypto_data_*.db 2>/dev/null | wc -l)
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Retained $REMAINING backup(s) in $BACKUP_DIR"
