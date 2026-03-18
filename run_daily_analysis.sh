#!/bin/bash
# Daily crypto data collection and analysis wrapper
# Runs full data collection, indicator calculation, and analysis

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Log file
LOG_FILE="daily_analysis.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=== Daily Crypto Analysis - $TIMESTAMP ===" >> "$LOG_FILE"

# Set up environment
echo "Setting up environment..." >> "$LOG_FILE"
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
# Don't source nvm.sh - it may change Node version
# Just ensure openclaw is in PATH

# Extract Coinbase environment variables from .zshrc
echo "Loading Coinbase credentials..." >> "$LOG_FILE"
if [ -f ~/.zshrc ]; then
    export COINBASE_API_KEY=$(grep '^export COINBASE_API_KEY=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    export COINBASE_API_SECRET=$(grep '^export COINBASE_API_SECRET=' ~/.zshrc | cut -d= -f2- | tr -d '"'"'")
    
    if [ -z "$COINBASE_API_KEY" ] || [ -z "$COINBASE_API_SECRET" ]; then
        echo "ERROR: Could not extract Coinbase credentials from .zshrc" >> "$LOG_FILE"
        echo "ERROR: Could not extract Coinbase credentials from .zshrc"
        exit 1
    fi
    
    echo "Coinbase credentials loaded" >> "$LOG_FILE"
else
    echo "ERROR: .zshrc not found" >> "$LOG_FILE"
    echo "ERROR: .zshrc not found"
    exit 1
fi

# Check Python availability
echo "Checking Python..." >> "$LOG_FILE"
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found" >> "$LOG_FILE"
    echo "ERROR: python3 not found"
    exit 1
fi

# Run daily collection with timeout (10 minutes)
echo "Running crypto_data_collector.py --daily (10min timeout)..." >> "$LOG_FILE"
timeout 600 python3 crypto_data_collector.py --daily >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
    echo "ERROR: Daily analysis timed out after 600 seconds" >> "$LOG_FILE"
    echo "❌ Daily analysis timed out"
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Daily analysis completed at $TIMESTAMP" >> "$LOG_FILE"
    
    # Check if report was generated
    if [ -f "portfolio_technical_report.txt" ]; then
        echo "Technical report generated: portfolio_technical_report.txt" >> "$LOG_FILE"
        
        # Send report via Telegram if OpenClaw is available
        if command -v openclaw &> /dev/null; then
            echo "Sending report to Telegram..." >> "$LOG_FILE"
            REPORT_CONTENT=$(head -c 4000 portfolio_technical_report.txt)  # Limit to Telegram max
            if openclaw message send --target $TELEGRAM_CHAT_ID --message "$REPORT_CONTENT" >> "$LOG_FILE" 2>&1; then
                echo "Report sent to Telegram" >> "$LOG_FILE"
                echo "✅ Daily analysis report sent to Telegram"
            else
                echo "WARNING: Failed to send report to Telegram" >> "$LOG_FILE"
                echo "⚠️ Analysis completed but Telegram send failed"
            fi
        else
            echo "WARNING: openclaw command not found, report not sent" >> "$LOG_FILE"
            echo "⚠️ Analysis completed but openclaw not available for Telegram"
        fi
    else
        echo "WARNING: portfolio_technical_report.txt not found" >> "$LOG_FILE"
        echo "⚠️ Analysis completed but report file not generated"
    fi
else
    echo "ERROR: Daily analysis failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    echo "❌ Daily analysis failed"
    
    # Send failure alert via Telegram if OpenClaw is available
    if command -v openclaw &> /dev/null; then
        echo "Sending failure alert to Telegram..." >> "$LOG_FILE"
        ALERT_MESSAGE="🚨 Daily crypto analysis FAILED with exit code $EXIT_CODE at $TIMESTAMP. Check logs: $LOG_FILE"
        if openclaw message send --target $TELEGRAM_CHAT_ID --message "$ALERT_MESSAGE" >> "$LOG_FILE" 2>&1; then
            echo "Failure alert sent to Telegram" >> "$LOG_FILE"
        else
            echo "WARNING: Failed to send failure alert to Telegram" >> "$LOG_FILE"
        fi
    fi
fi

echo "" >> "$LOG_FILE"

# Exit with the Python script's exit code
exit $EXIT_CODE