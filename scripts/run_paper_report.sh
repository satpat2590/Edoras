#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# End-of-day paper portfolio performance report
# Scheduled: Daily at 5:00 PM EDT (21:00 UTC)

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
echo "📈 Paper Portfolio Daily Report"
echo "Date: $(date)"
echo "================================================"

# Run paper trading report
python3 -m core.paper_trading --report

# Check if report was generated
if [ -f "reports/paper_portfolio_report.txt" ]; then
    echo "✅ Paper portfolio report generated"
    
    # Send via Telegram (using OpenClaw CLI)
    REPORT_CONTENT=$(cat reports/paper_portfolio_report.txt)
    
    # Truncate to 4000 characters (Telegram limit)
    if [ ${#REPORT_CONTENT} -gt 4000 ]; then
        REPORT_CONTENT="${REPORT_CONTENT:0:3997}..."
    fi
    
    # Escape single quotes for shell
    ESCAPED_REPORT=$(echo "$REPORT_CONTENT" | sed "s/'/'\"'\"'/g")
    
    # Send via OpenClaw
    send_telegram "$ESCAPED_REPORT" 2>/dev/null || \
        echo "⚠️ Failed to send via OpenClaw CLI"
    
    # Archive report
    mv reports/paper_portfolio_report.txt "reports/paper_$(date +%Y%m%d).txt" 2>/dev/null || \
        mv reports/paper_portfolio_report.txt "paper_portfolio_report_$(date +%Y%m%d).txt"
    
else
    echo "❌ Failed to generate paper portfolio report"
    # Send error alert
    send_telegram "❌ Paper portfolio report failed to generate on $(date)" 2>/dev/null || true
fi

echo "================================================"
echo "✅ Paper portfolio daily report completed"
echo "================================================"