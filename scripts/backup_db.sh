#!/bin/bash
# BrinChat Database Backup Script
# Keeps last 7 daily backups + weekly backups for 4 weeks

BRINCHAT_DIR="/home/tech/projects/BrinChat"
BACKUP_DIR="$BRINCHAT_DIR/backups"
DB_FILE="$BRINCHAT_DIR/peanutchat.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create daily backup
if [ -f "$DB_FILE" ]; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_DIR/daily_$TIMESTAMP.db'"
    echo "$(date): Created backup daily_$TIMESTAMP.db"
    
    # On Sundays (day 7), also create a weekly backup
    if [ "$DAY_OF_WEEK" -eq 7 ]; then
        cp "$BACKUP_DIR/daily_$TIMESTAMP.db" "$BACKUP_DIR/weekly_$TIMESTAMP.db"
        echo "$(date): Created weekly backup"
    fi
else
    echo "$(date): ERROR - Database file not found: $DB_FILE"
    exit 1
fi

# Clean up old daily backups (keep last 7)
ls -t "$BACKUP_DIR"/daily_*.db 2>/dev/null | tail -n +8 | xargs -r rm -f

# Clean up old weekly backups (keep last 4)
ls -t "$BACKUP_DIR"/weekly_*.db 2>/dev/null | tail -n +5 | xargs -r rm -f

echo "$(date): Cleanup complete. Current backups:"
ls -lh "$BACKUP_DIR"/*.db 2>/dev/null | awk '{print $9, $5}'
