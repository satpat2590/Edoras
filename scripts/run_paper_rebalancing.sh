#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# Weekly paper portfolio rebalancing
# Scheduled: Monday 9:00 AM EDT (13:00 UTC)

EDORAS_DIR="$HOME/edoras"
cd "$EDORAS_DIR"

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Set Python path
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

# Load Coinbase credentials from .zshrc
if [ -f ~/.zshrc ]; then
    source ~/.zshrc
fi

echo "================================================"
echo "🔄 Weekly Paper Portfolio Rebalancing"
echo "Date: $(date)"
echo "Day of week: $(date +%A)"
echo "================================================"

# Only run on Mondays (1 = Monday in cron)
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" != "1" ]; then
    echo "⚠️ Not Monday (day $DAY_OF_WEEK) - skipping rebalancing"
    exit 0
fi

# Run paper rebalancing
python3 -m core.paper_rebalancing

# Check exit code
if [ $? -eq 0 ]; then
    echo "✅ Weekly rebalancing completed successfully"
else
    echo "❌ Weekly rebalancing failed"
    # Send error alert
    send_telegram "❌ Paper portfolio weekly rebalancing failed on $(date)" 2>/dev/null || true
fi

echo "================================================"
echo "✅ Weekly rebalancing script completed"
echo "================================================"