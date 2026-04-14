#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# Price alerts cron job wrapper

set -euo pipefail

WORKSPACE="/home/satyamini/edoras"
LOG_DIR="$WORKSPACE/logs"
TARGET_CHAT="${TELEGRAM_CHAT_ID:-1806720995}"

if [ -f ~/.zshrc ]; then
    export COINBASE_API_KEY=$(grep '^export COINBASE_API_KEY=' ~/.zshrc | cut -d= -f2- | tr -d "'\"")
    export COINBASE_API_SECRET=$(grep '^export COINBASE_API_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d "'\"")
    export COINBASE_API=$(grep '^export COINBASE_API=' ~/.zshrc | cut -d= -f2- | tr -d "'\"")
    export COINBASE_SECRET=$(grep '^export COINBASE_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d "'\"")
fi

export PATH="/home/satyamini/miniconda3/bin:/home/satyamini/.nvm/versions/node/v22.22.1/bin:$PATH"
export PYTHONPATH="$WORKSPACE/src:${PYTHONPATH:-}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/price_alerts_$(date +%Y%m%d_%H%M%S).log"

echo "Starting price alert check at $(date)" | tee -a "$LOG_FILE"

if [ -z "${COINBASE_API_KEY:-}" ] && [ -z "${COINBASE_API:-}" ]; then
    echo "ERROR: No Coinbase API credentials found" | tee -a "$LOG_FILE"
    exit 1
fi

echo "Coinbase API credentials found" | tee -a "$LOG_FILE"

cd "$WORKSPACE"
echo "Running price alert check..." | tee -a "$LOG_FILE"

python3 -m reports.price_alerts --check --target "$TARGET_CHAT" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "Price alert check completed successfully at $(date)" | tee -a "$LOG_FILE"
else
    echo "Price alert check failed with exit code $EXIT_CODE at $(date)" | tee -a "$LOG_FILE"
fi

exit $EXIT_CODE
