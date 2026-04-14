#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDORAS_DIR="$(dirname "$SCRIPT_DIR")"
cd "$EDORAS_DIR"

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$HOME/Library/pnpm:$PATH"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

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

LOG_FILE="$EDORAS_DIR/logs/trading_agent.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== Trading Agent Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Run the trading agent
python3 -m llm.trading_agent --run >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS at $TIMESTAMP" >> "$LOG_FILE"
else
    echo "ERROR with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"

    # Send failure alert
    if command -v curl &> /dev/null; then
        send_telegram "Trading agent failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE" 2>&1 || true
    fi
fi

exit $EXIT_CODE
