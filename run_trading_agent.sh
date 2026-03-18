#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/Library/pnpm:$PATH"

# Load credentials
if [ -f ~/.config/coinbase.env ]; then
    set -a
    source ~/.config/coinbase.env
    set +a
fi

# Also source .zshrc for any additional env vars
if [ -f ~/.zshrc ]; then
    source ~/.zshrc 2>/dev/null || true
fi

LOG_FILE="trading_agent.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== Trading Agent Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Run the trading agent
python3 trading_agent.py --run >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS at $TIMESTAMP" >> "$LOG_FILE"
else
    echo "ERROR with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"

    # Send failure alert
    if command -v openclaw &> /dev/null; then
        openclaw message send --target $TELEGRAM_CHAT_ID --message "Trading agent failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE" 2>&1 || true
    fi
fi

exit $EXIT_CODE
