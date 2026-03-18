#!/bin/bash
# End-of-day paper portfolio performance report
# Scheduled: Daily at 5:00 PM EDT (21:00 UTC)

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
echo "📈 Paper Portfolio Daily Report"
echo "Date: $(date)"
echo "================================================"

# Run paper trading report
python3 paper_trading.py --report

# Check if report was generated
if [ -f "paper_portfolio_report.txt" ]; then
    echo "✅ Paper portfolio report generated"
    
    # Send via Telegram (using OpenClaw CLI)
    REPORT_CONTENT=$(cat paper_portfolio_report.txt)
    
    # Truncate to 4000 characters (Telegram limit)
    if [ ${#REPORT_CONTENT} -gt 4000 ]; then
        REPORT_CONTENT="${REPORT_CONTENT:0:3997}..."
    fi
    
    # Escape single quotes for shell
    ESCAPED_REPORT=$(echo "$REPORT_CONTENT" | sed "s/'/'\"'\"'/g")
    
    # Send via OpenClaw
    openclaw message send --target $TELEGRAM_CHAT_ID --message "$ESCAPED_REPORT" 2>/dev/null || \
        echo "⚠️ Failed to send via OpenClaw CLI"
    
    # Archive report
    mv paper_portfolio_report.txt "reports/paper_$(date +%Y%m%d).txt" 2>/dev/null || \
        mv paper_portfolio_report.txt "paper_portfolio_report_$(date +%Y%m%d).txt"
    
else
    echo "❌ Failed to generate paper portfolio report"
    # Send error alert
    openclaw sessions_send 'telegram:$TELEGRAM_CHAT_ID' "❌ Paper portfolio report failed to generate on $(date)" 2>/dev/null || true
fi

echo "================================================"
echo "✅ Paper portfolio daily report completed"
echo "================================================"