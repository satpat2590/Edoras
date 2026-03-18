#!/bin/bash
# Price alerts cron job wrapper

set -euo pipefail

# Configuration
WORKSPACE="/home/satyamini/.openclaw/workspace/projects/edoras"
LOG_DIR="$WORKSPACE/logs"
SCRIPT="$WORKSPACE/price_alerts.py"
TARGET_CHAT="$TELEGRAM_CHAT_ID"  # Owner's Telegram (set TELEGRAM_CHAT_ID)

# Set up environment - source .zshrc for Coinbase credentials
if [ -f ~/.zshrc ]; then
    # Extract Coinbase environment variables
    export COINBASE_API_KEY=$(grep '^export COINBASE_API_KEY=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API_SECRET=$(grep '^export COINBASE_API_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API=$(grep '^export COINBASE_API=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_SECRET=$(grep '^export COINBASE_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
fi

# Ensure PATH includes node for openclaw command
export PATH="/home/satyamini/.nvm/versions/node/v22.22.1/bin:$PATH"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Log file with timestamp
LOG_FILE="$LOG_DIR/price_alerts_$(date +%Y%m%d_%H%M%S).log"

echo "Starting price alert check at $(date)" | tee -a "$LOG_FILE"
echo "Working directory: $(pwd)" | tee -a "$LOG_FILE"

# Check if environment variables are set
if [ -z "${COINBASE_API_KEY:-}" ] && [ -z "${COINBASE_API:-}" ]; then
    echo "ERROR: No Coinbase API credentials found in environment" | tee -a "$LOG_FILE"
    echo "Please ensure COINBASE_API_KEY and COINBASE_API_SECRET or COINBASE_API and COINBASE_SECRET are set" | tee -a "$LOG_FILE"
    exit 1
fi

echo "Coinbase API credentials found" | tee -a "$LOG_FILE"

# Run the alert check
cd "$WORKSPACE"
echo "Running price alert check..." | tee -a "$LOG_FILE"

python3 "$SCRIPT" --check --target "$TARGET_CHAT" 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "Price alert check completed successfully at $(date)" | tee -a "$LOG_FILE"
else
    echo "Price alert check failed with exit code $EXIT_CODE at $(date)" | tee -a "$LOG_FILE"
    # Try to send error notification via OpenClaw
    openclaw message send --target "$TARGET_CHAT" --message "⚠️ Price alert check failed. Check logs." 2>/dev/null || true
fi

exit $EXIT_CODE