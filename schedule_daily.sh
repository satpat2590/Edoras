#!/bin/bash
# Daily scheduler wrapper for random portfolio reports

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Log file
LOG_FILE="scheduler.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=== Daily Scheduler Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Check Python availability
echo "Checking Python..." >> "$LOG_FILE"
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found" >> "$LOG_FILE"
    echo "ERROR: python3 not found"
    exit 1
fi

# Check at command availability
echo "Checking at command..." >> "$LOG_FILE"
if ! command -v at &> /dev/null; then
    echo "ERROR: at command not found" >> "$LOG_FILE"
    echo "ERROR: at command not found"
    exit 1
fi

# Run the scheduler
echo "Running schedule_random_reports.py..." >> "$LOG_FILE"
python3 schedule_random_reports.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Daily scheduler completed at $TIMESTAMP" >> "$LOG_FILE"
    echo "✅ Daily scheduler completed at $TIMESTAMP"
else
    echo "ERROR: Scheduler failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    echo "❌ Scheduler failed at $TIMESTAMP"
fi

echo "" >> "$LOG_FILE"

exit $EXIT_CODE