#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDORAS_DIR="$(dirname "$SCRIPT_DIR")"
cd "$EDORAS_DIR"

export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi
if [ -f ~/.zshrc ]; then
    source ~/.zshrc 2>/dev/null || true
fi

LOG_FILE="$EDORAS_DIR/logs/trading_agent_midday.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "=== Midday Review - $TIMESTAMP ===" >> "$LOG_FILE"

python3 -m llm.trading_agent --midday >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERROR exit $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    if command -v curl &> /dev/null; then
        send_telegram "Midday review failed (exit $EXIT_CODE) at $TIMESTAMP" 2>/dev/null || true
    fi
fi

exit $EXIT_CODE
