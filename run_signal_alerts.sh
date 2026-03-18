#!/bin/bash
# Signal alerts wrapper

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="signal_alerts.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=== Signal Alerts Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Set up environment for OpenClaw
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Check required environment variables
if [ -z "$COINBASE_API_KEY" ] || [ -z "$COINBASE_API_SECRET" ]; then
    echo "ERROR: COINBASE_API_KEY or COINBASE_API_SECRET not set in environment" >> "$LOG_FILE"
    echo "ERROR: COINBASE_API_KEY or COINBASE_API_SECRET not set in environment"
    exit 1
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "WARNING: OPENAI_API_KEY not set, sentiment analysis may fail" >> "$LOG_FILE"
    echo "WARNING: OPENAI_API_KEY not set, sentiment analysis may fail"
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found" >> "$LOG_FILE"
    echo "ERROR: python3 not found"
    exit 1
fi

# Run signal alerts with sentiment analysis
echo "Running signal_alerts.py --check --sentiment..." >> "$LOG_FILE"
python3 signal_alerts.py --check --sentiment >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Signal alerts completed at $TIMESTAMP" >> "$LOG_FILE"
    echo "✅ Signal alerts completed"
else
    echo "ERROR: Signal alerts failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    echo "❌ Signal alerts failed"
    
    # Send failure alert via Telegram if OpenClaw is available
    if command -v openclaw &> /dev/null; then
        echo "Sending failure alert to Telegram..." >> "$LOG_FILE"
        ALERT_MESSAGE="🚨 Signal alerts FAILED with exit code $EXIT_CODE at $TIMESTAMP. Check logs: $LOG_FILE"
        if openclaw message send --target $TELEGRAM_CHAT_ID --message "$ALERT_MESSAGE" >> "$LOG_FILE" 2>&1; then
            echo "Failure alert sent to Telegram" >> "$LOG_FILE"
        else
            echo "WARNING: Failed to send failure alert to Telegram" >> "$LOG_FILE"
        fi
    fi
fi

echo "" >> "$LOG_FILE"
exit $EXIT_CODE