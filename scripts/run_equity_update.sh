#!/bin/bash
set -e

EDORAS_DIR="$HOME/edoras"
cd "$EDORAS_DIR"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"
export PATH="$HOME/miniconda3/bin:$PATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi

python3 -m data.equity_data_collector --update
