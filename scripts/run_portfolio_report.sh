#!/bin/bash
source "$(dirname "$0")/lib_telegram.sh"
# Wrapper script for automated portfolio reports
# Called by 'at' scheduler

set -e  # Exit on error

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDORAS_DIR="$(dirname "$SCRIPT_DIR")"
cd "$EDORAS_DIR"
export PYTHONPATH="$EDORAS_DIR/src:$PYTHONPATH"

# Log file
LOG_FILE="$EDORAS_DIR/logs/run_portfolio_report.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "=== Portfolio Report Run - $TIMESTAMP ===" >> "$LOG_FILE"

# Source environment variables and set up nvm
echo "Setting up environment..." >> "$LOG_FILE"

# Set Node.js version directly via PATH
export PATH="$HOME/.nvm/versions/node/v22.22.1/bin:$PATH"
echo "Node version: $(node --version 2>&1)" >> "$LOG_FILE"

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

# Check OpenClaw CLI availability
echo "Checking OpenClaw CLI..." >> "$LOG_FILE"
if ! command -v curl &> /dev/null; then
    echo "ERROR: telegram helper not found in PATH" >> "$LOG_FILE"
    echo "ERROR: telegram helper not found in PATH"
    echo "PATH: $PATH" >> "$LOG_FILE"
    exit 1
fi

echo "OpenClaw CLI version: $(curl --version 2>&1)" >> "$LOG_FILE"

# Run the automated report
echo "Running automated portfolio report..." >> "$LOG_FILE"
python3 -m reports.automated_portfolio_report >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: Report completed at $TIMESTAMP" >> "$LOG_FILE"
    echo "✅ Report sent successfully at $TIMESTAMP"
else
    echo "ERROR: Report failed with exit code $EXIT_CODE at $TIMESTAMP" >> "$LOG_FILE"
    echo "❌ Report failed at $TIMESTAMP"
fi

echo "" >> "$LOG_FILE"

# Exit with the Python script's exit code
exit $EXIT_CODE