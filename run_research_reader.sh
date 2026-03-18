#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi
if [ -f ~/.zshrc ]; then
    source ~/.zshrc 2>/dev/null || true
fi

python3 research_reader.py --read --papers 3 2>&1
