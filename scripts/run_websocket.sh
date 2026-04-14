#!/bin/bash
# Coinbase WebSocket real-time market data feed
# Long-running daemon — started via systemd

EDORAS_DIR="$HOME/edoras"
cd "$EDORAS_DIR"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"
export PATH="$HOME/miniconda3/bin:$PATH"

if [ -f ~/.config/coinbase.env ]; then
    set -a; source ~/.config/coinbase.env; set +a
fi

exec python3 -u -m realtime.supervisor
