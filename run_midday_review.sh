#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi
if [ -f ~/.zshrc ]; then
    source ~/.zshrc 2>/dev/null || true
fi

LOG_FILE="trading_agent_midday.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== Midday Review - $TIMESTAMP ===" >> "$LOG_FILE"

python3 trading_agent.py --midday >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR exit $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    if command -v openclaw &> /dev/null; then
        openclaw message send --target $TELEGRAM_CHAT_ID --message "Midday review failed (exit $EXIT_CODE) at $TIMESTAMP" 2>/dev/null || true
    fi
fi

exit $EXIT_CODE
