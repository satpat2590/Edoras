#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# Daily crypto portfolio report cron job wrapper

set -euo pipefail

# Configuration
WORKSPACE="/home/satyamini/edoras"
LOG_DIR="$WORKSPACE/logs"
SCRIPT="$WORKSPACE/scripts/daily_portfolio_report.py"
TARGET_CHAT="$TELEGRAM_CHAT_ID"  # Owner's Telegram (set TELEGRAM_CHAT_ID)

# Set up environment - source .zshrc for Coinbase credentials
if [ -f ~/.zshrc ]; then
    # Extract Coinbase environment variables
    export COINBASE_API_KEY=$(grep '^export COINBASE_API_KEY=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API_SECRET=$(grep '^export COINBASE_API_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API=$(grep '^export COINBASE_API=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_SECRET=$(grep '^export COINBASE_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
fi

# Ensure PATH includes tools for Telegram delivery
export PATH="/home/satyamini/miniconda3/bin:/home/satyamini/.nvm/versions/node/v22.22.1/bin:$PATH"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Log file with timestamp
LOG_FILE="$LOG_DIR/daily_report_$(date +%Y%m%d_%H%M%S).log"

echo "Starting daily portfolio report at $(date)" | tee -a "$LOG_FILE"
echo "Working directory: $(pwd)" | tee -a "$LOG_FILE"

# Check if environment variables are set
if [ -z "${COINBASE_API_KEY:-}" ] && [ -z "${COINBASE_API:-}" ]; then
    echo "ERROR: No Coinbase API credentials found in environment" | tee -a "$LOG_FILE"
    echo "Please ensure COINBASE_API_KEY and COINBASE_API_SECRET or COINBASE_API and COINBASE_SECRET are set" | tee -a "$LOG_FILE"
    exit 1
fi

echo "Coinbase API credentials found" | tee -a "$LOG_FILE"

# Run the report script
cd "$WORKSPACE"
echo "Running report script in auto-send mode..." | tee -a "$LOG_FILE"

python3 "$SCRIPT" --auto-send --target "$TARGET_CHAT" 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "Daily portfolio report completed successfully at $(date)" | tee -a "$LOG_FILE"
else
    echo "Daily portfolio report failed with exit code $EXIT_CODE at $(date)" | tee -a "$LOG_FILE"
    # Try to send error notification via OpenClaw
    send_telegram "⚠️ Daily crypto portfolio report failed. Check logs." 2>/dev/null || true
fi

exit $EXIT_CODE