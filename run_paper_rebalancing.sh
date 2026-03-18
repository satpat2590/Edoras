#!/bin/bash
# Weekly paper portfolio rebalancing
# Scheduled: Monday 9:00 AM EDT (13:00 UTC)

cd /home/satyamini/.openclaw/workspace/projects/edoras

# Load environment
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"

# Set Python path
export PYTHONPATH="/home/satyamini/.openclaw/workspace/projects/edoras:$PYTHONPATH"

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
# Double-check day of week
DAY_OF_WEEK=$(date +%u)
if [ "$DAY_OF_WEEK" != "1" ]; then
    echo "⚠️ Not Monday (day $DAY_OF_WEEK) - skipping rebalancing"
    exit 0
fi

# Run paper rebalancing
python3 paper_rebalancing.py

# Check exit code
if [ $? -eq 0 ]; then
    echo "✅ Weekly rebalancing completed successfully"
else
    echo "❌ Weekly rebalancing failed"
    # Send error alert
    openclaw sessions_send 'telegram:$TELEGRAM_CHAT_ID' "❌ Paper portfolio weekly rebalancing failed on $(date)" 2>/dev/null || true
fi

echo "================================================"
echo "✅ Weekly rebalancing script completed"
echo "================================================"