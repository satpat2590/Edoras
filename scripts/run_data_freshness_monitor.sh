#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDORAS_DIR="$(dirname "$SCRIPT_DIR")"
cd "$EDORAS_DIR"

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="/home/satyamini/miniconda3/bin:$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi

# Run freshness check with gap detection
python3 -m data.data_freshness_monitor --gaps 2>&1